#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info

from __future__ import print_function, absolute_import, unicode_literals, division
import six

import os
import sys
import argparse

import vtk # @UnresolvedImport"

from bronx.syntax.parsing import str2dict
from bronx.syntax.pretty import smooth_string
from bronx.meteo import constants
from footprints import proxy as fpx

# Automatically set the python path
package_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, package_path)
sys.path.insert(0, os.path.join(package_path, 'site'))
import epygram
from epygram import epylog, epygramError
from epygram.args_catalog import (add_arg_to_parser,
                                  files_management, fields_management,
                                  misc_options, output_options,
                                  runtime_options, graphical_options,
                                  extraction_options)
from epygram.base import FieldSet
from usevtk import Usevtk

import numpy

def _get_field(resource, fieldseed, subzone, zoom, operation,
               composition, pressure_unit_hpa, global_shift_center,
               Yconvert, cheap_height, empty_value=None):    
    """
    Get field and prepare it
    :param resource: resource in which field is read
    :param fieldseed: field identifier
    :param subzone: LAM zone among ('C', 'CI', None).
    :param zoom: a dict(lonmin, lonmax, latmin, latmax) on which to build the plot.
    :param operation: makes the requested operation
                   (e.g. {'operation':'-','operand':273.15} or
                   {'operation':'exp'}) on the field before plot.
    :param composition: dict containing info for a composition of fields.(cf. code for doc)
    :param pressure_unit_hpa: converts pressure fields to hPa.
    :param global_shift_center: for global lon/lat grids, shift the center by the
                         requested angle (in degrees). Enables a [0,360] grid
                         to be shifted to a [-180,180] grid, for instance (with -180 argument).
    :param Yconvert: among ('pressure', 'height', 'altitude'),
                  to convert the vertical coordinate.
                  For height/altitude, implies the read of T and q
                  profiles and optionally pressure departure, hydrometeors.
    :param cheap_height: if True, do not take hydrometeors nor pressure departure
                      into account for computing height from pressure.
    :param empty_value: levels with all data set to empty_value are suppressed
                        to minimize memory print
    :return: field prepared for plotting, updated subzone, academic switch
    """
    map_vcoord = {'pressure':100,
                  'altitude':102,
                  'height':103}
    Yconvert = map_vcoord.get(Yconvert, Yconvert)
    
    field = resource.extract_subdomain(fieldseed, None, vertical_coordinate=Yconvert,
                                       interpolation='nearest', cheap_height=cheap_height)
    assert field.structure == '3D', \
           ' '.join(['Oops ! Looks like',
                     str(fieldseed),
                     'is not known as a 3D Field by epygram.'])
    if subzone is None:
        subzone = 'CI'
    elif subzone == 'CIE':
        subzone = None
    if not field.geometry.grid.get('LAMzone', False):
        subzone = None
    if field.spectral:
        field.sp2gp()
    if operation is not None:
        field.operation(**operation)
    if global_shift_center is not None:
        field.global_shift_center(global_shift_center)
    if composition is not None:
        if 'file' in composition:
            toreadin = epygram.formats.resource(composition['file'], 'r')
        else:
            toreadin = resource
        composefield = toreadin.readfield(composition['fid'])
        assert isinstance(composefield, epygram.fields.H2DField), \
               ' '.join(['Oops ! Looks like',
                         str(fieldseed),
                         'is not known as a 3D Field by epygram.'])
        if 'file' in composition:
            del toreadin
        if 'preset' in composition:
            composefield.operation(composition['preset'])
        field.operation(composition['operation'], operand=composefield)
    if pressure_unit_hpa and \
       (field.fid['generic'].get('discipline') == 0 and
        field.fid['generic'].get('parameterCategory') == 3 and
        field.fid['generic'].get('parameterNumber') in (0, 1, 2, 8, 11, 25)):
        field.operation('/', 100.)
    
    academic = field.geometry.name == 'academic' #done here because extract_zoom looses this info
    
    if zoom not in (None, {}):
        field = field.extract_zoom(zoom)
    
    if empty_value is not None:
        fieldset = FieldSet()
        for k in range(len(field.geometry.vcoordinate.levels)):
            f = field.getlevel(k=k)
            if not numpy.all(f.getdata() == empty_value):
                fieldset.append(f)
        field = fpx.field(fid=field.fid,
                          structure=field.structure,
                          fieldset=fieldset)
    
    if hasattr(field, 'as_real_field'):
        field = field.as_real_field()
    data = field.getdata()
    field.setdata(data.astype({numpy.float64:numpy.float32, numpy.int64:numpy.int32}.get(data.dtype)))
    
    return field, subzone, academic

def _do_plot(field, plotmode,
             background_color, window_size, hide_axes, offscreen, title,
             z_factor, subzone, specificproj,
             minmax, colormin, colormax, opacity, opacitymin, opacitymax,
             vectors_subsampling, levelsnumber, streamlines_time, vectors_scale_factor,
             existing_rendering, viewport_pos, outline):
    """
    :param field: field to plot
    :param background_color: must be a color name or a 3-tuple.
    :param window_size: must be a 2-tuple.
    :param hide_axes: True to hide the axes representation.
    :param offscreen: True to hide window (useful when we only
                      want to produce png file instead of
                      interactively viewing the window).
    :param title: title to use on window.
    :param z_factor: factor to apply on z values (to modify aspect ratio of the plot).
    :param subzone: LAM zone among ('C', 'CI', None).
    :param specificproj: specific projection name or 'geoId'.
    :param minmax: tuple giving (or not) min and max fields values to be plotted.
    :param colormin, colormax: color to associate to min/max values (name or RGB values as a tuple).
    :param opacity: opacity for mode in ('contour', 'color', 'vectors')
    :param opacitymin, opacitymax: opacity to associate to mon/max values,
                                   for mode == 'volume'.
    :param vectors_subsampling: subsampling ratio of vectors plots.
    :param levelsnumber: number of color isolines for contour plot.
    :param streamlines_time: integration time for streamlines.
    :param vectors_scale_factor: scale factor to apply on vectors.
    :param existing_rendering: rendering to use for enabling several
                               viewports in a same window
    :param viewport_pos: position of viewport in window
    :param outline: must be None or a color name to plot outline
    """
    
    rendering = Usevtk(background_color, window_size,
                       hCoord=specificproj, z_factor=z_factor, offset=None,
                       reverseZ=None,
                       hide_axes=hide_axes, offscreen=offscreen, title=title,
                       maximum_number_of_peels=30, #useful, at least,  for contour plots with several levels and opacity!=1
                       from_field=field,
                       existing_rendering=existing_rendering, viewport_pos=viewport_pos)
    rendering.plotBorder('White' if background_color == 'Black' else 'Black')

    if plotmode != 'donothing':
        #Defining the color and alpha trasfer functions
        if plotmode in ('contour', 'color', 'volume'):
            #scalar field
            if minmax is None:
                minmax = field.min(), field.max()
            else:
                try:
                    m = float(minmax[0])
                except ValueError:
                    m = field.min()
                try:
                    M = float(minmax[1])
                except ValueError:
                    M = field.max()
                minmax = (m, M)
        elif plotmode in ('vectors', 'streamlines'):
            #vector field
            ff = field.to_module()
            if minmax is None:
                minmax = ff.min(), ff.max()
            else:
                try:
                    m = float(minmax[0])
                except ValueError:
                    m = ff.min()
                try:
                    M = float(minmax[1])
                except ValueError:
                    M = ff.max()
                minmax = (m, M)
            
        colorequal = colormin == colormax
        if isinstance(colormin, six.string_types):
            colormin = vtk.vtkNamedColors().GetColor3d(colormin)
        if isinstance(colormax, six.string_types):
            colormax = vtk.vtkNamedColors().GetColor3d(colormax)
        color = vtk.vtkColorTransferFunction()
        color.AddRGBPoint(minmax[0], *colormin)
        color.AddRGBPoint(minmax[1], *colormax)
        if plotmode == 'volume':
            opacity = vtk.vtkPiecewiseFunction()
            opacity.AddPoint(minmax[0], float(opacitymin))
            opacity.AddPoint(minmax[1], float(opacitymax))
        
        if plotmode == 'contour':
            levels = numpy.linspace(minmax[0], minmax[1], levelsnumber)
            field.plot3DContour(rendering,
                                levels, color, opacity=opacity,
                                colorbar=levelsnumber > 1 and not colorequal,
                                subzone=subzone)
        elif plotmode == 'color':
            field.plot3DColor(rendering,
                              color, opacity=opacity,
                              colorbar=not colorequal,
                              subzone=subzone)
        elif plotmode == 'volume':
            field.plot3DVolume(rendering,
                               minmax, color, opacity,
                               colorbar=not colorequal,
                               subzone=subzone)
        elif plotmode == 'vectors':
            field.plot3DVector(rendering,
                               vectors_subsampling, vectors_scale_factor,
                               color, opacity,
                               colorbar=not colorequal,
                               subzone=subzone)
        elif plotmode == 'streamlines':
            field.plot3DStream(rendering,
                               vectors_subsampling,
                               streamlines_time, tubesRadius=0.1,
                               color=color,
                               opacity=opacity,
                               plot_tube=False,
                               colorbar=not colorequal,
                               subzone=subzone)
        else:
            raise epygramError("This plot mode does not exist: " + str(plotmode))
        if outline is not None:
            field.plot3DOutline(rendering, color=outline, subzone=subzone)
    return rendering

def main(plotmode,
         filename,
         fieldseed,
         refname=None,
         diffonly=False,
         computewind=False,
         subzone=None,
         operation=None,
         diffoperation=None,
         pressure_unit_hpa=False,
         background_color='Black',
         window_size=(800, 800),
         hide_axes=False,
         ground=None,
         legend=None,
         output=False,
         outputfilename=None,
         specificproj=None,
         z_factor=None,
         minmax=None,
         diffminmax=None,
         levelsnumber=50,
         difflevelsnumber=50,
         vectors_subsampling=20,
         vectors_scale_factor=1.,
         diffvectors_scale_factor=1.,
         streamlines_time=1.,
         diffstreamlines_time=1.,
         colormin='Blue',
         colormax='Red',
         diffcolormin='Blue',
         diffcolormax='Red',
         opacity=1,
         opacitymin=1.,
         opacitymax=1.,
         diffopacitymin=1.,
         diffopacitymax=1.,
         zoom=None,
         resolution_increase=1.,
         composition=None,
         global_shift_center=None,
         focal_point=None,
         camera=None,
         Yconvert=None,
         cheap_height=True,
         verbose=False,
         empty_value=None,
         diffsamewindow=True,
         outline=None
         ):
    """
    Plot fields.

    :param plotmode: kind of plot among ('contour', 'color', 'volume', 'vectors', 'streamlines').
    :param filename: name of the file to be processed.
    :param fieldseed: field identifier.
    :param refname: name of the reference file to be compared to.
    :param diffonly: if True, only plots the difference field.
    :param computewind: from fieldseed, gets U and V components of wind, and
                     computes the module; plots barbs and module together.
    :param subzone: LAM zone among ('C', 'CI', None).
    :param operation: makes the requested operation
                   (e.g. {'operation':'-','operand':273.15} or
                   {'operation':'exp'}) on the field before plot.
    :param diffoperation: makes the requested operation
                       (e.g. {'operation':'-','operand':273.15} or
                       {'operation':'exp'}) on the difference field before plot.
    :param background_color: background color of the plotting window.
    :param window_size: tuple representing width and height of the plotting window.
    :param hide_axes: True to hide the axe arrows.
    :param ground: 'bluemarble' or a url (using '${z}', '${x}' and '${y}' as place holders)
    :param pressure_unit_hpa: converts FA log(pressure) fields to hPa.
    :param legend: legend to be written over plot.
    :param output: output format, among ('png', 'pdf', False). Overwritten by
                outputfilename.
    :param outputfilename: specify an output filename for the plot
                        (completed by output format).
    :param specificproj: specific projection name (cf.
                      :mod:`epygram.fields.H2DField`.plotfield() doc) or 'geoid'.
    :param z_factor: factor to apply on z values (to modify aspect ratio of the plot).
    :param minmax: tuple giving (or not) min and max fields values to be plotted.
    :param diffminmax: idem for difference fields.
    :param levelsnumber: number of color discretization/isolines for fields plots.
    :param difflevelsnumber: idem for difference fields.
    :param vectors_subsampling: subsampling ratio of vectors plots.
    :param vectors_scale_factor: scale factor to apply on arrow length.
    :param diffvectors_scale_factor: scale factor to apply on arrow length of diff plot.
    :param streamlines_time: integration time for streamlines.
    :param diffstreamlines_time: integration time for streamlines of diff plot.
    :param colormin, colormax: color to associate to min/max values (name or RGB values as a tuple).
    :param diffcolormin, diffcolormax: color to associate to min/max values of diff plot.
    :param opacity: opacity for mode in ('contour', 'color', 'vectors').
    :param opacitymin, opacitymax: opacity to associate to min/max values,
                                   for mode == 'volume'.
    :param diffopacitymin, diffopacitymax: opacity to associate to mon/max values of diff plot.
    :param zoom: a dict(lonmin, lonmax, latmin, latmax) on which to build the plot.
    :param resolution_increase: multiplicative factor to enhance resolution.
    :param composition: dict containing info for a composition of fields.(cf. code for doc)
    :param global_shift_center: for global lon/lat grids, shift the center by the
                             requested angle (in degrees). Enables a [0,360] grid
                             to be shifted to a [-180,180] grid, for instance (with -180 argument).
    :param focal_point: focal point of the camera, as a (x, y, z) tuple.
    :param camera: position of the camera, as a (x, y, z) tuple.
    :param Yconvert: among ('pressure', 'height', 'altitude'),
                  to convert the vertical coordinate.
                  For height/altitude, implies the read of T and q
                  profiles and optionally pressure departure, hydrometeors.
    :param cheap_height: if True, do not take hydrometeors nor pressure departure
                      into account for computing height from pressure.
    :param verbose: True to be verbose
    :param empty_value: levels with all data set to empty_value are suppressed
                        to minimize memory print
    :param diffsamewindow: if True uses several renderers in the same window in diff mode
    :param outline: must be None or a color name to plot outline
    """
    if outputfilename and not output:
        raise epygramError('*output* format must be defined if outputfilename is supplied.')
    epylog.warning("""This tool is quite new: please report anything suspect""")

    resource = epygram.formats.resource(filename, openmode='r')
    diffmode = refname is not None
    if diffmode:
        reference = epygram.formats.resource(refname, openmode='r')

    if diffsamewindow:
        plot_numbers_by_window = (1 if diffonly else 3) if diffmode else 1
    else:
        plot_numbers_by_window = 1

    rendering = []
    if not computewind:
        field, subzone, academic = _get_field(resource, fieldseed, subzone, zoom, operation,
                                              composition, pressure_unit_hpa, global_shift_center,
                                              Yconvert, cheap_height, empty_value)
        if legend is not None:
            title = legend
        else:
            title = str(fieldseed) + " - " + str(field.validity.get())
            if diffmode:
                title = resource.container.basename + " : " + title 
    else:
        assert len(fieldseed) == 2
        (Ufid, Vfid) = fieldseed
        
        U, subzone, academic = _get_field(resource, Ufid, subzone, zoom, operation,
                                          None, False, global_shift_center,
                                          Yconvert, cheap_height, empty_value)
        V, subzone, academic  = _get_field(resource, Vfid, subzone, zoom, operation,
                                           None, False, global_shift_center,
                                           Yconvert, cheap_height, empty_value)
        field = epygram.fields.make_vector_field(U, V)
        if legend is not None:
            title = legend
        else:
            title = str(fieldseed) + " - " + str(U.validity.get())
            if diffmode:
                title = resource.container.basename + " : " + title
    if not diffonly:
        if academic:
            specificproj = 'll'
        ren_res = _do_plot(field, plotmode,
                           background_color, window_size, hide_axes, output, title,
                           z_factor, subzone, specificproj,
                           minmax, colormin, colormax, opacity, opacitymin, opacitymax,
                           vectors_subsampling, levelsnumber, streamlines_time, vectors_scale_factor,
                           existing_rendering=None,
                           viewport_pos=(0., 0., 1., 1.) if plot_numbers_by_window == 1 else (0., 0., 1./3, 1.),
                           outline=outline)
        rendering.append(ren_res)
    if diffmode:
        if not computewind:
            reffield, subzone, academic = _get_field(reference, fieldseed, subzone, zoom, operation,
                                                     None, pressure_unit_hpa, global_shift_center,
                                                     Yconvert, cheap_height, empty_value)
            if legend is not None:
                title = legend
            else:
                title = reference.container.basename + " : " + str(fieldseed) + "\n" + str(reffield.validity.get())
        else:
            U, subzone, academic = _get_field(reference, Ufid, subzone, zoom, operation,
                                              None, False, global_shift_center,
                                              Yconvert, cheap_height, empty_value)
            V, subzone, academic = _get_field(reference, Vfid, subzone, zoom, operation,
                                              None, False, global_shift_center,
                                              Yconvert, cheap_height, empty_value)
            reffield = epygram.fields.make_vector_field(U, V)
            if legend is not None:
                title = legend
            else:
                title = reference.container.basename + " : " + str(fieldseed) + "\n" + str(U.validity.get())
        if not diffonly:
            if academic:
                specificproj = 'll'
            ren_ref = _do_plot(reffield, plotmode,
                               background_color, window_size, hide_axes, output, title,
                               z_factor, subzone, specificproj,
                               minmax, colormin, colormax, opacity, opacitymin, opacitymax,
                               vectors_subsampling, levelsnumber, streamlines_time, vectors_scale_factor,
                               existing_rendering=None if plot_numbers_by_window == 1 else ren_res,
                               viewport_pos=(0., 0., 1., 1.) if plot_numbers_by_window == 1 else (1./3, 0., 2./3, 1.),
                               outline=outline)
            rendering.append(ren_ref)
        if legend is not None:
            title = legend
        else:
            title = resource.container.basename + " - " + reference.container.basename + " : " + str(fieldseed)
        diff = field - reffield
        if diffoperation is not None:
            diff.operation(**diffoperation)
        if academic:
            specificproj = 'll'
        ren_dif = _do_plot(diff, plotmode,
                           background_color, window_size, hide_axes, output, title,
                           z_factor, subzone, specificproj,
                           diffminmax, diffcolormin, diffcolormax, opacity, diffopacitymin, diffopacitymax,
                           vectors_subsampling, difflevelsnumber, diffstreamlines_time, diffvectors_scale_factor,
                           existing_rendering=None if plot_numbers_by_window == 1 else ren_res,
                           viewport_pos=(0., 0., 1., 1.) if plot_numbers_by_window == 1 else (2./3, 0., 1., 1.),
                           outline=outline)
        rendering.append(ren_dif)

    if ground is not None:
        l = []
        if not diffonly:
            l.append((field, ren_res))
        if diffmode and not diffonly:
            l.append((reffield, ren_ref))
        if diffmode:
            l.append((diff, ren_dif))
        zs = None
        for f, ren in l:
            geometry = f.geometry.deepcopy()
            if geometry.vcoordinate.typeoffirstfixedsurface not in [102, 103]:
                raise epygramError("We can plot something on ground only with a height or altitude coordinate")
            if geometry.vcoordinate.typeoffirstfixedsurface == 102:
                if zs is None:
                    fids = resource.listfields(complete=True)
                    for fid in fids:
                        hg = (fid['generic'].get('discipline', 255),
                              fid['generic'].get('parameterCategory', 255),
                              fid['generic'].get('parameterNumber', 255),
                              fid['generic'].get('typeOfFirstFixedSurface', 255))
                        if hg in ((0, 3, 4, 1), (0, 193, 5, 1), (0, 3, 5, 1), (0, 3, 6, 1), (2, 0, 7, 1)):
                            #(0, 193, 5) is for SPECSURFGEOPOTEN
                            zs = resource.readfield(fid[resource.format])
                            if zs.spectral:
                                zs.sp2gp()
                            if hg in ((0, 3, 4, 1), (0, 193, 5, 1)):
                                zs.setdata(zs.getdata() / constants.g0)
                            break
                    if zs is None:
                        raise epygramError("No terrain height field found, ground cannot be plotted")
                geometry.vcoordinate = zs.as_vcoordinate(force_kind=102)
            else:
                kwargs_vcoord = {'structure': 'V',
                                 'typeoffirstfixedsurface': 103,
                                 'position_on_grid': geometry.vcoordinate.position_on_grid,
                                 'grid': geometry.vcoordinate.grid,
                                 'levels': [0]}
                geometry.vcoordinate = fpx.geometry(**kwargs_vcoord)
            if ground == 'bluemarble':
                actor, _ = geometry.plot3DBluemarble(ren, interpolation='nearest', subzone=subzone)
            else:
                actor, _ = geometry.plot3DMaptiles(ren, ground, 2, interpolation='nearest', subzone=subzone)
            actor.GetProperty().LightingOff()

    #Rendering and cameras synchronization
    for ren in rendering:
        ren.window.Render()
    cam = rendering[0].renderer.GetActiveCamera()
    for ren in rendering[1:]:
        ren.renderer.SetActiveCamera(cam)
    proj3d = rendering[0].proj3d
    if focal_point is not None:
        cam.SetFocalPoint(*proj3d(*focal_point))
    if camera is not None:
        cam.SetPosition(*proj3d(*camera))
    for ren in rendering:
        ren.renderer.ResetCameraClippingRange()
    def cameraMove(*args):
        if verbose:
            print("Camera:")
            print("  Position                (vtk coordinates) :", cam.GetPosition())
            print("  Position                (true coordinates):", tuple([c[0] for c in proj3d(*cam.GetPosition(), inverse=True)]))
            print("  Focal Point             (vtk coordinates) :", cam.GetFocalPoint())
            print("  Focal Point             (true coordinates):", tuple([c[0] for c in proj3d(*cam.GetFocalPoint(), inverse=True)]))
            print("  View up                 (vtk coordinates) :", cam.GetViewUp())
            print("  Distance                (vtk coordinates) :", cam.GetDistance())
            print("  Direction of projection (vtk coordinates) :", cam.GetDirectionOfProjection())
            print("  Roll                    (vtk coordinates) :", cam.GetRoll())
            print("  Clipping range          (vtk coordinates) :", cam.GetClippingRange())
        for ren in rendering:
            ren.window.Render()
    for ren in rendering:
        ren.interactor.AddObserver("EndInteractionEvent", cameraMove)
    cameraMove() #for printing position

    # Output
    if output:
        if output not in ('png'):
            raise NotImplementedError("This output format is not (yet) implemented with 3D view: " + str(output))
        epylog.info("save plots...")
        suffix = '.'.join(['plot', output])
        parameter = smooth_string(fieldseed)
        # main resource
        if not diffonly:
            if not diffmode and outputfilename:
                outputfile = '.'.join([outputfilename, output])
            else:
                outputfile = '.'.join([resource.container.abspath,
                                       parameter,
                                       suffix])
            ren_res.write_png(outputfile, resolution_increase)
        # reference
        if diffmode and not diffonly:
            outputfile = '.'.join([reference.container.abspath,
                                   parameter,
                                   suffix])
            ren_ref.write_png(outputfile, resolution_increase)
        # diff
        if diffmode:
            if not outputfilename:
                outputfile = resource.container.absdir + \
                             '.'.join(['diff',
                                       resource.container.basename + '-' + reference.container.basename,
                                       parameter,
                                       suffix])
            else:
                outputfile = '.'.join([outputfilename, output])
            ren_dif.write_png(outputfile, resolution_increase)
    else:
        rendering[0].interactor.Start()

# end of main() ###############################################################

if __name__ == '__main__':

    # 1. Parse arguments
    ####################
    parser = argparse.ArgumentParser(description='An EPyGrAM tool for simple 3d plots\
                                                  of meteorological fields from a resource.',
                                     epilog='End of help for: %(prog)s (EPyGrAM v' + epygram.__version__ + ')')

    add_arg_to_parser(parser, graphical_options['plotmode'])
    add_arg_to_parser(parser, files_management['principal_file'])
    add_arg_to_parser(parser, fields_management['field'])
    add_arg_to_parser(parser, fields_management['windfieldU'])
    add_arg_to_parser(parser, fields_management['windfieldV'])
    diffmodes = parser.add_mutually_exclusive_group()
    add_arg_to_parser(diffmodes, files_management['file_to_refer_in_diff'])
    add_arg_to_parser(diffmodes, files_management['file_to_refer_in_diffonly'])
    add_arg_to_parser(parser, output_options['output'])
    add_arg_to_parser(parser, output_options['outputfilename'])
    add_arg_to_parser(parser, misc_options['LAMzone'])
    add_arg_to_parser(parser, graphical_options['specific_map_projection'],
                      help=graphical_options['specific_map_projection'][-1]['help'] + \
                           "\nAlternatively, this option can be 'geoid' to plot the " + \
                           "field around the geoid.")
    add_arg_to_parser(parser, graphical_options['minmax'])
    add_arg_to_parser(parser, graphical_options['diffminmax'])
    add_arg_to_parser(parser, graphical_options['levels_number'])
    add_arg_to_parser(parser, graphical_options['diff_levels_number'])
    add_arg_to_parser(parser, graphical_options['legend'])
    add_arg_to_parser(parser, graphical_options['hide_axes'])
    add_arg_to_parser(parser, graphical_options['vectors_subsampling'])
    add_arg_to_parser(parser, graphical_options['vectors_verticalsubsampling'])
    add_arg_to_parser(parser, graphical_options['vectors_scale_factor'])
    add_arg_to_parser(parser, graphical_options['diffvectors_scale_factor'])
    add_arg_to_parser(parser, graphical_options['streamlines_time'])
    add_arg_to_parser(parser, graphical_options['diffstreamlines_time'])
    add_arg_to_parser(parser, graphical_options['colorminmax'])
    add_arg_to_parser(parser, graphical_options['diffcolorminmax'])
    add_arg_to_parser(parser, graphical_options['alpha'])
    add_arg_to_parser(parser, graphical_options['alphaminmax'])
    add_arg_to_parser(parser, graphical_options['diffalphaminmax'])
    add_arg_to_parser(parser, graphical_options['lonlat_zoom'])
    add_arg_to_parser(parser, graphical_options['z_factor'])
    add_arg_to_parser(parser, graphical_options['resolution_increase'])
    add_arg_to_parser(parser, graphical_options['window_size'])
    add_arg_to_parser(parser, graphical_options['ground'])
    add_arg_to_parser(parser, graphical_options['background_color'])
    add_arg_to_parser(parser, graphical_options['global_shift_center'])
    add_arg_to_parser(parser, graphical_options['focal_point'])
    add_arg_to_parser(parser, graphical_options['camera'])
    Y = parser.add_mutually_exclusive_group()
    add_arg_to_parser(Y, extraction_options['verticalcoord2pressure'])
    add_arg_to_parser(Y, extraction_options['verticalcoord2height'])
    add_arg_to_parser(Y, extraction_options['verticalcoord2altitude'])
    add_arg_to_parser(parser, extraction_options['no_cheap_height_conversion'])
    add_arg_to_parser(parser, misc_options['pressure_unit_hpa'])
    add_arg_to_parser(parser, misc_options['operation_on_field'])
    add_arg_to_parser(parser, misc_options['diffoperation_on_field'])
    add_arg_to_parser(parser, misc_options['composition_with_field'])
    add_arg_to_parser(parser, runtime_options['verbose'])

    args = parser.parse_args()

    # 2. Initializations
    ####################
    epygram.init_env()
    # 2.0 logs
    epylog.setLevel('WARNING')
    if args.verbose:
        epylog.setLevel('INFO')

    # 2.1 options
    if args.Drefname is not None:
        refname = args.Drefname
        diffonly = True
    else:
        refname = args.refname
        diffonly = False
    diffmode = refname is not None
    if args.minmax is not None:
        minmax = args.minmax.split(',')
    else:
        minmax = None
    if args.diffminmax is not None:
        diffminmax = args.diffminmax.split(',')
    else:
        diffminmax = None
    if args.zoom is not None:
        zoom = str2dict(args.zoom, float)
    else:
        zoom = None
    if args.operation is not None:
        _operation = args.operation.split(',')
        operation = {'operation':_operation.pop(0).strip()}
        if len(_operation) > 0:
            operation['operand'] = float(_operation.pop(0).strip())
    else:
        operation = None
    if args.diffoperation is not None:
        _diffoperation = args.diffoperation.split(',')
        diffoperation = {'operation':_diffoperation.pop(0).strip()}
        if len(_diffoperation) > 0:
            diffoperation['operand'] = float(_diffoperation.pop(0).strip())
    else:
        diffoperation = None
    if args.compose_with is not None:
        _composition = args.compose_with.split(',')
        composition = {'fid':_composition.pop(0).strip(),
                       'operation':_composition.pop(0).strip()}
        while len(_composition) > 0:
            composition.update(str2dict(_composition.pop(0).strip()))
    else:
        composition = None
    if args.projection is not None and 'nsper' in args.projection:
        specificproj = ('nsper', {})
        for item in args.projection.split(',')[1:]:
            k, v = item.replace('=', ':').split(':')
            specificproj[1][k.strip()] = float(v)
    else:
        specificproj = args.projection
    if args.window_size is not None:
        window_size = [int(s) for s in args.window_size.split(',')]
    else:
        window_size = None
    if args.colorminmax is not None:
        colormin, colormax = args.colorminmax.split(',')
        colormin, colormax = colormin.strip(), colormax.strip()
    else:
        colormin, colormax = 'Blue', 'Red'
    if args.diffcolorminmax is not None:
        diffcolormin, diffcolormax = args.diffcolorminmax.split(',')
        diffcolormin, diffcolormax = diffcolormin.strip(), diffcolormax.strip()
    else:
        diffcolormin, diffcolormax = 'Blue', 'Red'
    if args.alphaminmax is not None:
        alphamin, alphamax = args.alphaminmax.split(',')
    else:
        alphamin, alphamax = 1., 1.
    if args.diffalphaminmax is not None:
        diffalphamin, diffalphamax = args.diffalphaminmax.split(',')
    else:
        diffalphamin, diffalphamax = 1., 1.
    vectors_subsampling = dict(x=1, y=1, z=1)
    if args.vectors_subsampling is not None:
        vectors_subsampling['x'] = int(args.vectors_subsampling)
        vectors_subsampling['y'] = int(args.vectors_subsampling)
    if args.vectors_verticalsubsampling is not None:
        vectors_subsampling['z'] = int(args.vectors_verticalsubsampling)
    if args.focal_point is not None:
        focal_point =[float(s) for s in args.focal_point.split(',')]
    else:
        focal_point = None
    if args.camera is not None:
        camera =[float(s) for s in args.camera.split(',')]
    else:
        camera = None

    # 2.2 field to be processed
    computewind = False
    if args.field is not None:
        fieldseed = args.field
        assert args.plotmode in ('contour', 'color', 'volume', 'donothing'), \
               "plotmode must be in ('contour', 'color', 'volume') for scalar field"
    elif args.Ucomponentofwind is not None or args.Vcomponentofwind is not None:
        fieldseed = (args.Ucomponentofwind, args.Vcomponentofwind)
        assert args.plotmode in ('vectors', 'streamlines', 'donothing'), \
               "plotmode must be in ('vectors', 'streamlines') for vector field"
        if None in fieldseed:
            raise epygramError("wind mode: both U & V components of wind must be supplied")
        computewind = True
        if diffmode:
            raise NotImplementedError("diffmode (-d/D) AND wind mode (--wU/wV) options together.")
    else:
        raise epygramError("Need to specify a field (-f) or two wind fields (--wU/--wV).")

    # 3. Main
    #########
    main(args.plotmode,
         args.filename,
         fieldseed,
         refname=refname,
         diffonly=diffonly,
         computewind=computewind,
         subzone=args.zone,
         operation=operation,
         diffoperation=diffoperation,
         pressure_unit_hpa=args.pressure_unit_hpa,
         background_color=args.background_color,
         window_size=window_size,
         hide_axes=args.hide_axes,
         ground=args.ground,
         legend=args.legend,
         output=args.output,
         outputfilename=args.outputfilename,
         specificproj=specificproj,
         z_factor=args.z_factor,
         minmax=minmax,
         diffminmax=diffminmax,
         levelsnumber=args.levelsnumber,
         difflevelsnumber=args.difflevelsnumber,
         vectors_subsampling=vectors_subsampling,
         vectors_scale_factor=args.vectors_scale_factor,
         diffvectors_scale_factor=args.diffvectors_scale_factor,
         streamlines_time=args.streamlines_time,
         diffstreamlines_time=args.diffstreamlines_time,
         colormin=colormin,
         colormax=colormax,
         diffcolormin=diffcolormin,
         diffcolormax=diffcolormax,
         opacity=args.alpha,
         opacitymin=alphamin,
         opacitymax=alphamax,
         diffopacitymin=diffalphamin,
         diffopacitymax=diffalphamax,
         zoom=zoom,
         resolution_increase=args.resolution_increase,
         composition=composition,
         global_shift_center=args.global_shift_center,
         focal_point=focal_point,
         camera=camera,
         Yconvert=args.Yconvert,
         cheap_height=args.cheap_height,
         verbose=args.verbose,
         empty_value=None,  # if useful, add it to the command line options
         diffsamewindow=True,  # could also be added to command line options
         outline=None #could also be added to command line options
         )

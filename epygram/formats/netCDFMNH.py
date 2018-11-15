#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
Contains the class to handle the Meso-NH netCDF format.
"""


#TODO
#reprendre le moveonmass de LFI dans l'autre branche
#pb: pas de difference sur les profils en 102 et 103:
#python -c "import epygram; r = epygram.formats.resource('16J36.1.SEG01.001.nc', 'r'); f=r.extractprofile('UT', 0., 42.68, vertical_coordinate=102); print f; f.plotfield(); import matplotlib.pyplot as plt; plt.show()"
#méthode _get_special_value
#methode _set_special_value: si existe déjà, compare les valeurs, sinon écrit


from __future__ import print_function, absolute_import, unicode_literals, division

import datetime
import os
import copy
import numpy
import math
import re
import sys
import six

import footprints
from footprints import FPDict, proxy as fpx
from bronx.datagrip.misc import read_dict_in_CSV

import netCDF4

from epygram import config, epygramError, util
from epygram.util import Angle
from epygram.base import FieldSet, FieldValidity, Field
from epygram.resources import FileResource
from epygram.fields import H2DField, MiscField, D3Field
from epygram.geometries.VGeometry import hybridH2altitude, hybridH2pressure

__all__ = ['netCDFMNH']

epylog = footprints.loggers.getLogger(__name__)

gridIndicatorDict = {0:('__unknown__', '__unknown__'),
                     1:('center', 'mass'),
                     2:('center-left', 'mass'),
                     3:('lower-center', 'mass'),
                     4:('center', 'flux'),
                     5:('lower-left', 'mass'),
                     6:('center-left', 'flux'),
                     7:('lower-center', 'flux'),
                     8:('lower-left', 'flux')}

def inquire_field_dict(fieldname):
    """
    Returns the info contained in the netCDFMNH_field_dict for the requested field.
    """
    matching_field = None
    for fd in netCDFMNH._field_dict:
        dictitem = fd['name']
        pattern = re.subn('\.', r'\.', dictitem)[0]  # protect '.'
        pattern = pattern.replace('?', '.')  # change unix '?' to python '.' (any char)
        pattern = pattern.replace('*', '.*')  # change unix '*' to python '.*' (several any char)
        pattern += '(?!.)'
        if re.match(pattern, fieldname):
            matching_field = fd
            break

    if matching_field is None:
        epylog.info("field '" + fieldname + "' is not referenced in Field_Dict_netCDFMNH. Assume its type being a MiscField.")
        matching_field = {'name':fieldname, 'type':'Misc'}

    return copy.deepcopy(matching_field)

class netCDFMNH(FileResource):
    """
    Class implementing all specificities for MesoNH netCDF resource format.
    """

    _footprint = dict(
        attr=dict(
            format=dict(
                values=set(['netCDFMNH']),
                default='netCDFMNH')
        )
    )

    # the Field Dictionary gathers info about fields nature
    CSV_field_dictionaries = config.netCDFMNH_field_dictionaries_csv
    # syntax: _field_dict = [{'name':'fieldname1', 'type':'...', ...}, {'name':'fieldname2', 'type':'...', ...}, ...]
    _field_dict = []

    @classmethod
    def _read_field_dict(cls, fd_abspath):
        """Reads the CSV fields dictionary of the format."""
        field_dict, file_priority = read_dict_in_CSV(fd_abspath)
        if file_priority == 'main':
            cls._field_dict = field_dict
        elif file_priority == 'underwrite':
            for fd in field_dict:
                found = False
                for cfd in cls._field_dict:
                    if fd['name'] == cfd['name']:
                        found = True
                        break
                if not found:
                    cls._field_dict.append(fd)
        elif file_priority == 'overwrite':
            for cfd in cls._field_dict:
                found = False
                for fd in field_dict:
                    if fd['name'] == cfd['name']:
                        found = True
                        break
                if not found:
                    field_dict.append(cfd)
            cls._field_dict = field_dict

    def __init__(self, *args, **kwargs):
        self.isopen = False
        self.geometry = None
        self.validity = None

        # At creation of the first netCDFMNH, initialize netCDFMNH._field_dict
        if self._field_dict == []:
            self._read_field_dict(self.CSV_field_dictionaries['default'])
            if os.path.exists(self.CSV_field_dictionaries['user']):
                self._read_field_dict(self.CSV_field_dictionaries['user'])

        super(netCDFMNH, self).__init__(*args, **kwargs)
        if self.openmode in ('r', 'a'):
            try:
                guess = netCDF4.Dataset(self.container.abspath, self.openmode)
                if 'MASDEV' not in guess.variables:
                    raise IOError("this resource is not a netCDFMNH one.")
            except (RuntimeError, UnicodeEncodeError):
                raise IOError("this resource is not a netCDF one.")
            else:
                guess.close()

        if not self.fmtdelayedopen:
            self.open()

    def open(self, openmode=None):
        """
        Opens the file.

        :param openmode: optional, to open with a specific openmode, eventually
                         different from the one specified at initialization.
        """

        super(netCDFMNH, self).open(openmode=openmode)
        self._nc = netCDF4.Dataset(self.container.abspath, self.openmode)
        self.isopen = True
        self.empty = self.isopen == 'w'

    def close(self):
        """Closes a Meso-NH netCDF file."""
        if self.isopen:
            self._nc.close()
        self.isopen = False

################
# ABOUT FIELDS #
################
    def _get_generic_fid(self, fieldname):
        """Return a generic fid from fieldname (via Field Dict)."""
        if fieldname in self._nc.variables and not True in [dim.startswith(dname)
                                                            for dim in self._nc.variables[fieldname].dimensions
                                                            for dname in ['ni', 'nj', 'level', 'time']]:
            #This is a Misc field (no physical dimensions
            #No need to call inquire_field_dict
            fid = {'name':fieldname, 'type':'Misc'}
        else:
            fid = inquire_field_dict(fieldname)
        if fid['type'] == '3D':
            if self.geometry is None:
                self._read_geometry()
            fid['typeOfFirstFixedSurface'] = self.geometry.vcoordinate.typeoffirstfixedsurface
        fid.pop('type')
        fid.pop('name')

        return fid
    
    def find_fields_in_resource(self, seed=None, fieldtype=[], generic=False):
        """
        Returns a list of the fields from resource whose identifier match the given seed.

        :param seed: might be:\n
          - a regular expressions,
          - a list of regular expressions
          - *None*. If *None* (default), returns the list of all fields in resource.
        :param fieldtype: optional, among ('H2D', 'Misc') or a list of these strings.
          If provided, filters out the fields not of the given types.
        :param generic: if True, returns a list of tuples (fieldname, generic fid) of
          the fields.
        """
        if isinstance(fieldtype, list):
            fieldtypeslist = list(fieldtype)
        else:
            fieldtypeslist = [fieldtype]
        fieldslist = []
        def fill_fieldslist(tmplist):
            for f in tmplist:
                if fieldtypeslist == [] or inquire_field_dict(f)['type'] in fieldtypeslist:
                    fieldslist.append(f)
        if seed is None:
            tmplist = self.listfields()
            fill_fieldslist(tmplist)
        elif isinstance(seed, six.string_types):
            tmplist = util.find_re_in_list(seed, self.listfields())
            fill_fieldslist(tmplist)
        elif isinstance(seed, list):
            tmplist = []
            for s in seed:
                tmplist += self.find_fields_in_resource(seed=s)
            fill_fieldslist(tmplist)
        else:
            raise epygramError("seed must be a list, None or a string")
        if fieldslist == []:
            raise epygramError("no field matching '" + str(seed) + "' was found in resource " + self.container.abspath)

        if generic:
            fieldslist = [(f, self._get_generic_fid(f)) for f in fieldslist]

        return fieldslist

    def listfields(self, **kwargs):
        """
        Returns a list containing the netCDFMNH identifiers of all the fields of the
        resource.
        """
        return super(netCDFMNH, self).listfields(**kwargs)

    @FileResource._openbeforedelayed
    def _listfields(self, complete=False):
        """
        Actual listfields() method.

        :param complete: - if True method returns a list of {'netCDFMNH':fid,
                           'generic':generic_fid}
                         - if False method return a list of fid
        """
        fieldslist = self._nc.variables.keys()

        if complete:
            return [{'netCDFMNH':f, 'generic':self._get_generic_fid(f)} for f in fieldslist]
        else:
            return fieldslist

    def sortfields(self):
        """
        Returns a sorted list of fields with regards to their name and nature,
        as a dict of lists.
        """
        listMisc = []
        list3D = []
        list2D = []

        for field in self.listfields():
            info = inquire_field_dict(field)
            if info['type'] == 'H2D':
                list2D.append(field)
            elif info['type'] == '3D':
                list3D.append(field)
            else:
                listMisc.append(field)

        # sort
        list2D.sort()
        list3D.sort()
        listMisc.sort()

        outlists = {'3D fields':list(set(list3D)),
                    '2D fields':list(set(list2D)),
                    'Misc-fields':list(set(listMisc))}

        return outlists

    @FileResource._openbeforedelayed
    def readfield(self, fieldidentifier, getdata=True):
        """
        Reads one field, given its name and returns a Field instance.

        :param fieldidentifier: field name.
        :param getdata: optional, if *False*, only metadata are read, the field do not contain data.
                        Default is *True*.
        """
        if not isinstance(fieldidentifier, six.string_types):
            raise epygramError("fieldidentifier of a netCDFMNH file is a string.")

        # Get field info
        field_info = inquire_field_dict(fieldidentifier)
        if field_info['type'] in ['H2D', '3D']:
            if self.geometry is None:
                self._read_geometry()
            if self.validity is None:
                self._read_validity()

            # Make geometry object
            kwargs_geom = dict(structure=field_info['type'],
                               name=self.geometry.name,
                               grid=self.geometry.grid,
                               dimensions=self.geometry.dimensions,
                               geoid=config.netCDFMNH_default_geoid,
                               position_on_grid=None,
                               projection=self.geometry.projection  # Also used for academic geometries
                               )

            if self.geometry.vcoordinate is not None:
                # vertical geometry
                kwargs_vcoord = {'structure': 'V',
                                 'typeoffirstfixedsurface': self.geometry.vcoordinate.typeoffirstfixedsurface,
                                 'position_on_grid': self.geometry.vcoordinate.position_on_grid,
                                 'grid': copy.copy(self.geometry.vcoordinate.grid),
                                 'levels': copy.copy(self.geometry.vcoordinate.levels)}
                if field_info['type'] == 'H2D' and 'level' not in field_info:
                    field_info['level'] = 0
                for k in field_info:
                    if k == 'typeOfFirstFixedSurface':
                        kwargs_vcoord['typeoffirstfixedsurface'] = field_info[k]
                    elif k == 'level':
                        kwargs_vcoord['levels'] = [field_info[k]]
                if field_info['type'] != '3D':
                    kwargs_vcoord.pop('grid', None)

        # Get field metadata
        var = self._nc.variables[fieldidentifier]
        (h, v) = gridIndicatorDict[var.grid]
        gridIndicator = {'vertical':v, 'horizontal':h}
        comment = var.comment

        # Create field
        if field_info['type'] in ['H2D', '3D']:
            # Create H2D field
            fid = {self.format: fieldidentifier,
                   'generic':FPDict(self._get_generic_fid(fieldidentifier))
                   }
            kwargs_geom['position_on_horizontal_grid'] = gridIndicator['horizontal']
            kwargs_vcoord['position_on_grid'] = gridIndicator['vertical']
            kwargs_geom['vcoordinate'] = fpx.geometry(**kwargs_vcoord)
            geometry = fpx.geometry(**kwargs_geom)
            field = fpx.field(fid=fid,
                              structure=geometry.structure,
                              geometry=geometry, validity=self.validity.deepcopy(),
                              processtype='forecast', comment=comment)
        elif field_info['type'] == 'Misc':
            # Create Misc field
            fid = {self.format: fieldidentifier,
                   'generic': FPDict()
                   }
            class empty(object): pass
            geometry = empty()
            geometry.position_on_horizontal_grid = gridIndicator['horizontal']
            geometry.vcoordinate = empty()
            geometry.vcoordinate.position_on_grid = gridIndicator['vertical']
            field = MiscField(fid=fid, comment=comment)
            field.geometry = geometry
        if getdata:
            if field_info['type'] == 'H2D':
                # Only one horizontal level
                data = geometry.reshape_data(var[...].flatten())
            elif field_info['type'] == '3D':
                # 3D data
                data = var[...].reshape((len(self.geometry.vcoordinate.grid['gridlevels']) + 1,
                                        self.geometry.dimensions['X'] * self.geometry.dimensions['Y']))
                data = geometry.reshape_data(data, 'Z')
            field.setdata(data)

        return field

    def readfields(self, requestedfields=None, getdata=True):
        """
        Returns a :class:`epygram.base.FieldSet` containing requested fields read in the resource.

        :param requestedfields: might be \n
          - a string or a regular expression (e.g. 'R?T' or 'RCT')
          - a list of strings or regular expressions (e.g. ['COVER???', 'RVT'])
          - if not specified, interpretated as all fields that will be found in resource
        :param getdata: optional, if *False*, only metadata are read, the fields do not contain data.
                        Default is *True*.
        """

        requestedfields = self.find_fields_in_resource(requestedfields)
        if requestedfields == []:
            raise epygramError("unable to find requested fields in resource.")

        return super(netCDFMNH, self).readfields(requestedfields, getdata)

    def writefield(self, field):
        """
        Write a field in the resource.

        :param field: the field to write to the resource.
        """
        if not isinstance(field, Field):
            raise epygramError("*field* must be a Field instance.")

        fieldset = FieldSet()
        fieldset.append(field)
        self.writefields(fieldset)

#    def writefields(self, fieldset):
#        """
#        Write the fields of the *fieldset* in the resource.
#
#        :param fieldset: must be a :class:`epygram.base.FieldSet` instance.
#        """
#
#        if not isinstance(fieldset, FieldSet):
#            raise epygramError("'fieldset' argument must be of kind FieldSet.")
#        if self.openmode == 'r':
#            raise IOError("cannot write field in a LFI with openmode 'r'.")
#
#        specialValues = dict()
#        specialNames = ['LFI_COMPRESSED', 'CARTESIAN',
#                        'LAT0', 'LON0',
#                        'LATORI', 'LATOR', 'LONORI', 'LONOR',
#                        'RPK', 'BETA',
#                        'IMAX', 'JMAX', 'KMAX',
#                        'XHAT', 'YHAT', 'ZHAT',
#                        'DTEXP%TDATE', 'DTEXP%TIME',
#                        'DTCUR%TDATE', 'DTCUR%TIME',
#                        'SLEVE']
#        if self.true3d:
#            specialFields = specialNames
#        else:
#            specialFields = [(name, None) for name in specialNames]
#        specialFieldsComments = {'LFI_COMPRESSED':'Compressed articles',
#                                 'CARTESIAN':'Logical for cartesian geometry'.ljust(100),
#                                 'LAT0':'reference latitude for conformal projection (DEGREES)'.ljust(100),
#                                 'LON0':'reference longitude for conformal projection (DEGREES)'.ljust(100),
#                                 'LATORI':'DEGREES'.ljust(100),
#                                 'LATOR':'DEGREES'.ljust(100),
#                                 'LONORI':'DEGREES'.ljust(100),
#                                 'LONOR':'DEGREES'.ljust(100),
#                                 'RPK':''.ljust(100),
#                                 'BETA':'rotation angle (DEGREES)'.ljust(100),
#                                 'IMAX':''.ljust(100),
#                                 'JMAX':''.ljust(100),
#                                 'KMAX':''.ljust(100),
#                                 'XHAT':'Position x in the conformal or cartesian plane (METERS)'.ljust(100),
#                                 'YHAT':'Position y in the conformal or cartesian plane (METERS)'.ljust(100),
#                                 'ZHAT':'height level without orography (METERS)'.ljust(100),
#                                 'DTEXP%TDATE':'YYYYMMDD',
#                                 'DTEXP%TIME':'SECONDS',
#                                 'DTCUR%TDATE':'YYYYMMDD',
#                                 'DTCUR%TIME':'SECONDS',
#                                 'SLEVE':''.ljust(100)}
#        specialFieldsGridIndicator = {'LFI_COMPRESSED':0,
#                                      'CARTESIAN':0,
#                                      'LAT0':0,
#                                      'LON0':0,
#                                      'LATORI':0,
#                                      'LATOR':0,
#                                      'LONORI':0,
#                                      'LONOR':0,
#                                      'RPK':0,
#                                      'BETA':0,
#                                      'IMAX':0,
#                                      'JMAX':0,
#                                      'KMAX':0,
#                                      'XHAT':2,
#                                      'YHAT':3,
#                                      'ZHAT':4,
#                                      'DTEXP%TDATE':4,
#                                      'DTEXP%TIME':4,
#                                      'DTCUR%TDATE':4,
#                                      'DTCUR%TIME':4,
#                                      'SLEVE':4}
#        writtenfields = self.listfields()
#        has2D = False
#        has3D = False
#        for fid in writtenfields:
#            name = fid if self.true3d else fid[0]
#            field_info = inquire_field_dict(name)
#            if field_info['type'] == '3D':
#                has3D = True
#            if field_info['type'] == 'H2D' and name != 'ZS' and '.DATIM' not in name:
#                has2D = True
#        if has2D or has3D:
#            if self._compressed is None:
#                self._read_compression()
#            specialValues['LFI_COMPRESSED'] = self._compressed
#        if has3D:
#            if ('SLEVE', None) in writtenfields:
#                specialValues['SLEVE'] = self.readfield(('SLEVE', None))
#            else:
#                specialValues['SLEVE'] = False
#
#        myFieldset = FieldSet()
#        fieldsMTO = []
#        appendedfields = []
#        for field in fieldset:
#            keep = True
#            fid = field.fid[self.format]
#            # This field must not be already written, except if this is a special record
#            # because special records can be written to file automaticaly when the first H2DField is encountered
#            if fid in writtenfields and fid not in specialFields:
#                raise epygramError("there already is a field with the same name in this LFI.")
#            elif fid in writtenfields:
#                keep = False
#                # In case of a special record already written in file, value must be the same
#                if self.readfield(fid).getdata() != field.getdata():
#                    raise epygramError("there already is a field with the same name in this LFI with a different value.")
#            # check for level validity
#            if isinstance(field, H2DField) or isinstance(field, D3Field):
#                if (not self.true3d) and (not isinstance(fid[1], int)):
#                    raise epygramError("the level of a 2D field must be an integer")
#                fieldsMTO.append(field)
#            else:
#                if (not self.true3d) and fid[1] is not None:
#                    raise epygramError("the level of a non 2D field must be None")
#            if fid in appendedfields:
#                raise epygramError("a same field cannot be written twice in a LFI.")
#            appendedfields.append(fid)
#            if keep:
#                myFieldset.append(field)
#
#        if not self.isopen:
#            self.open()
#
#        # geometry, validity and compression
#        for field in fieldsMTO:
#            gvff = self._get_geometryValidity_from_field(field)
#            for record, value in gvff.items():
#                # Cache value for next field
#                if record not in specialValues:
#                    if (record if self.true3d else (record, None)) in appendedfields:
#                        for f in myFieldset:
#                            if (f.fid if self.true3d else f.fid[0]) == record:
#                                specialValues[record] = f.getdata()
#                    elif (record if self.true3d else (record, None)) in writtenfields:
#                        specialValues[record] = self.readfield(record if self.true3d else (record, None)).getdata()
#                    else:
#                        specialValues[record] = value
#                        if record == 'LFI_COMPRESSED':
#                            keep = value  # we keep it only if its value is True
#                        else:
#                            keep = True
#                        if keep:
#                            comment = specialFieldsComments[record]
#                            (h, v) = gridIndicatorDict[specialFieldsGridIndicator[record]]
#                            class empty(object): pass
#                            geometry = empty()
#                            geometry.position_on_horizontal_grid = h
#                            geometry.vcoordinate = empty()
#                            geometry.vcoordinate.position_on_grid = v
#                            f = MiscField(fid={self.format:record if self.true3d else (record, None)}, comment=comment)
#                            f.setdata(value)
#                            f.geometry = geometry
#                            myFieldset.append(f)
#                if record in ['LAT0', 'LON0', 'LATOR', 'LATORI', 'LONOR', 'LONORI', 'RPK', 'BETA', 'ZHAT']:
#                    # Float comparisons
#                    special_rpk = (record == 'RPK' and
#                                   field.geometry.secant_projection and
#                                   field.geometry.name not in ['mercator', 'polar_stereographic'])
#                    if special_rpk:
#                        # In geometries, when seant, we store latin1 and latin2 which are computed from RPK
#                        # Computation is not exact computing back RPK from latin1 and latin2 does not give exactly the same result
#                        latin1_field = field.geometry.projection['secant_lat1'].get('degrees')
#                        latin2_field = field.geometry.projection['secant_lat2'].get('degrees')
#                        if 'latin1' not in specialValues:
#                            specialValues['latin1'], specialValues['latin2'] = self._get_latin1_latin2_lambert(gvff['LAT0'], specialValues['RPK'])
#                        check = numpy.all(util.nearlyEqualArray([latin1_field, latin2_field],
#                                                                [specialValues['latin1'].get('degrees'), specialValues['latin2'].get('degrees')])) or \
#                                util.nearlyEqual(value, specialValues[record])
#                    else:
#                        check = numpy.all(util.nearlyEqualArray(value, specialValues[record]))
#                elif record in ['XHAT', 'YHAT']:
#                    # We check deltaX and deltaY because XHAT and YHAT can be computed in two different ways (prep_ideal or prep_pgd)
#                    check = util.nearlyEqualArray(value[1] - value[0],
#                                                  specialValues[record][1] - specialValues[record][0])
#                else:
#                    check = numpy.all(value == specialValues[record])
#                if not check:
#                    raise epygramError("this field is not compatible with the fields already written to the file: " + record)
#        # writing
#        done = []
#        for iField in range(len(myFieldset)):
#            if iField not in done:
#                field = myFieldset[iField]
#                field_info = inquire_field_dict(field.fid[self.format] if self.true3d else field.fid[self.format][0])
#                if field_info['type'] == '3D' and not self.true3d:
#                    comment = None
#                    gridIndicator = None
#                    dataInt = None
#                    for level in range(len(field.geometry.vcoordinate.grid['gridlevels']) + 1):
#                        f = None
#                        for i in range(len(myFieldset)):
#                            if myFieldset[i].fid[self.format] == (field.fid[self.format][0], level):
#                                f = myFieldset[i]
#                                num = i
#                        if f is None:
#                            raise epygramError("All levels of a 3D field must be written at once.")
#                        if not isinstance(f, H2DField):
#                            raise epygramError("All fields composing a 3D field must be H2DField")
#                        if comment is None:
#                            comment = f.comment
#                        elif comment != f.comment:
#                            raise epygramError("All fields composing a same 3D field must have the same comment.")
#                        (h, v) = f.geometry.position_on_horizontal_grid, f.geometry.vcoordinate.position_on_grid
#                        for key, value in gridIndicatorDict.items():
#                            if value == (h, v):
#                                mygridIndicator = key
#                        if gridIndicator is None:
#                            gridIndicator = mygridIndicator
#                        elif gridIndicator != mygridIndicator:
#                            raise epygramError("All fields composing a same 3D field must have the same position on grid.")
#                        done.append(num)
#                        if dataInt is None:
#                            dataInt = numpy.ndarray(field.getdata().size * (len(field.geometry.vcoordinate.grid['gridlevels']) + 1), dtype='int64')
#                        dataInt[level * field.getdata().size:(level + 1) * field.getdata().size] = numpy.ma.copy(f.getdata()).view('int64').flatten()
#                else:
#                    if (isinstance(field, H2DField) and not self.true3d) or (isinstance(field, D3Field) and self.true3d):
#                        dataInt = numpy.ma.copy(field.getdata()).view('int64').flatten()
#                    else:  # Misc type
#                        data = field.getdata()
#                        if 'int' in field.datatype.name:
#                            dataInt = data.flatten()
#                        elif 'str' in field.datatype.name:
#                            if field.shape in ((1,), ()):
#                                dataInt = numpy.array([ord(d) for d in str(data)])
#                            else:
#                                raise NotImplementedError('writing string arrays is not implemented.')
#                        elif 'float' in field.datatype.name:
#                            dataInt = data.view('int64').flatten()
#                        elif 'bool' in field.datatype.name:
#                            dataInt = numpy.array(data, dtype=numpy.int64).flatten()
#                        else:
#                            raise NotImplementedError("writing of datatype " + field.datatype.name + " is not implemented.")
#                    comment = field.comment
#                    (h, v) = field.geometry.position_on_horizontal_grid, field.geometry.vcoordinate.position_on_grid
#                    for key, value in gridIndicatorDict.items():
#                        if value == (h, v):
#                            gridIndicator = key
#                header = numpy.ndarray(2 + len(comment), dtype=numpy.int64)
#                header[0] = gridIndicator
#                header[1] = len(comment)
#                for i in range(len(comment)):
#                    header[2 + i] = ord(comment[i])
#                name = field.fid[self.format] if self.true3d else field.fid[self.format][0]
#                dataToWrite = numpy.concatenate((header, dataInt))
#                wlfi.wlfiecr(self._unit, name, len(dataToWrite), dataToWrite)
#
#                if self.empty:
#                    self.empty = False

#    def rename_field(self, fid, new_fid):
#        """Renames a field "in place"."""
#        wlfi.wlfiren(self._unit, fid if self.true3d else fid[0], new_fid if self.true3d else new_fid[0])
#
#    def delfield(self, fid):
#        """Deletes a field from file "in place"."""
#        wlfi.wlfisup(self._unit, fid if self.true3d else fid[0])
#
    @FileResource._openbeforedelayed
    def extractprofile(self, fid, lon=None, lat=None,
                       geometry=None,
                       vertical_coordinate=None,
                       interpolation='nearest',
                       external_distance=None,
                       cheap_height=None):
        """
        Extracts a vertical profile from the netCDFMNH resource, given its fid
        and the geographic location (*lon*/*lat*) of the profile.

        :param fid: must have syntax: 'PARAMETER' PARAMETER being the name of the
                    parameter requested, as named in the file.
        :param lon: the longitude of the desired point.
        :param lat: the latitude of the desired point.
        :param geometry: the geometry on which extract data. If None, it is built from
          lon/lat.
        :param vertical_coordinate: defines the requested vertical coordinate of the
          V1DField (cf. :class:`epygram.geometries.V1DGeometry` coordinate
          possible values).
        :param interpolation: defines the interpolation function used to compute
          the profile at requested lon/lat from the fields grid:\n
          - if 'nearest' (default), extracts profile at the horizontal nearest neighboring gridpoint;
          - if 'linear', computes profile with horizontal linear spline interpolation;
          - if 'cubic', computes profile with horizontal cubic spline interpolation.
        :param external_distance: can be a dict containing the target point value
          and an external field on the same grid as self, to which the distance
          is computed within the 4 horizontally nearest points; e.g.
          {'target_value':4810, 'external_field':an_H2DField_with_same_geometry}.
          If so, the nearest point is selected with
          distance = |target_value - external_field.data|
        :param cheap_height: has no effect (compatibity with FA format)
        """
        if geometry is None:
            if None in [lon, lat]:
                raise epygramError("You must give a geometry or lon *and* lat")
            if self.geometry is None:
                self._read_geometry()
            pointG = self.geometry.make_profile_geometry(lon, lat)
        else:
            if lon is not None or lat is not None:
                raise epygramError("You cannot provide lon or lat when geometry is given")
            if geometry.structure != "V1D":
                raise epygramError("geometry must be a V1D")
            pointG = geometry

        profile = self.extract_subdomain(fid, pointG,
                                         interpolation=interpolation,
                                         vertical_coordinate=vertical_coordinate)

        return profile

    @FileResource._openbeforedelayed
    def extractsection(self, fid, end1=None, end2=None,
                       geometry=None,
                       points_number=None,
                       resolution=None,
                       vertical_coordinate=None,
                       interpolation='linear',
                       cheap_height=None):
        """
        Extracts a vertical section from the netCDFMNH resource, given its fid
        and the geographic (lon/lat) coordinates of its ends.
        The section is returned as a V2DField.

        :param fid: must have syntax: 'PARAMETER' PARAMETER being the name of the
                    parameter requested, as named in the file.
        :param end1: must be a tuple (lon, lat).
        :param end2: must be a tuple (lon, lat).
        :param geometry: the geometry on which extract data. If None, defaults to
          linearily spaced positions computed from  *points_number*.
        :param points_number: defines the total number of horizontal points of the
          section (including ends). If None, defaults to a number computed from
          the *ends* and the *resolution*.
        :param resolution: defines the horizontal resolution to be given to the
          field. If None, defaults to the horizontal resolution of the field.
        :param vertical_coordinate: defines the requested vertical coordinate of the
          V2DField (cf. :class:`epygram.geometries.V1DGeometry` coordinate
          possible values).
        :param interpolation: defines the interpolation function used to compute
          the profile points locations from the fields grid: \n
          - if 'nearest', each horizontal point of the section is
            taken as the horizontal nearest neighboring gridpoint;
          - if 'linear' (default), each horizontal point of the section is
            computed with linear spline interpolation;
          - if 'cubic', each horizontal point of the section is
            computed with linear spline interpolation.
        :param cheap_height: has no effect (compatibity with FA format)
        """
        if geometry is None:
            if None in [end1, end2]:
                raise epygramError("You must give a geometry or end1 *and* end2")
            if self.geometry is None:
                self._read_geometry()
            sectionG = self.geometry.make_section_geometry(end1, end2,
                                                           points_number=points_number,
                                                           resolution=resolution)
        else:
            if end1 is not None or end2 is not None:
                raise epygramError("You cannot provide end1 or end2 when geometry is given")
            if geometry.structure != "V2D":
                raise epygramError("geometry must be a V2D")
            sectionG = geometry

        section = self.extract_subdomain(fid, sectionG,
                                         interpolation=interpolation,
                                         vertical_coordinate=vertical_coordinate)

        return section

    @FileResource._openbeforedelayed
    def extract_subdomain(self, fid, geometry,
                          vertical_coordinate=None,
                          interpolation='linear',
                          exclude_extralevels=True,
                          cheap_height=None):
        """
        Extracts a subdomain from the netCDFMNH resource, given its fid
        and the geometry to use.

        :param fid: must have syntax: 'PARAMETER' PARAMETER being the name of the
                    parameter requested, as named in the file.
        :param geometry: the geometry on which extract data.
        :param vertical_coordinate: defines the requested vertical coordinate of the
          V2DField (cf. :class:`epygram.geometries.V1DGeometry` coordinate
          possible values).
        :param interpolation defines the interpolation function used to compute
          the profile points locations from the fields grid: \n
          - if 'nearest', each horizontal point of the section is
            taken as the horizontal nearest neighboring gridpoint;
          - if 'linear' (default), each horizontal point of the section is
            computed with linear spline interpolation;
          - if 'cubic', each horizontal point of the section is
            computed with linear spline interpolation.
        :param cheap_height: has no effect (compatibity with FA format)
        """
        field3d = self.readfield(fid)

        subdomain = field3d.extract_subdomain(geometry,
                                              interpolation=interpolation,
                                              exclude_extralevels=True)

        # vertical coords conversion
        if vertical_coordinate not in (None, subdomain.geometry.vcoordinate.typeoffirstfixedsurface):
            if subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 118 and \
               vertical_coordinate in (102, 103):
                zsfield = self.readfield('ZS')
                zs_values = zsfield.getvalue_ll(*geometry.get_lonlat_grid(),
                                                interpolation=interpolation, one=False)
                subdomain.geometry.vcoordinate = hybridH2altitude(subdomain.geometry.vcoordinate,
                                                                  zs_values,
                                                                  gridposition=subdomain.geometry.vcoordinate.position_on_grid,
                                                                  conv2height=(vertical_coordinate == 103))
            elif subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 118 and \
                   vertical_coordinate == 100:
                try:
                    P3d = self.readfield('PABSM')
                except:
                    P3d = self.readfield('PABST')
                P = P3d.extract_subdomain(geometry,
                                          interpolation=interpolation,
                                          exclude_extralevels=True)
                subdomain.geometry.vcoordinate = hybridH2pressure(subdomain.geometry.vcoordinate,
                                                                  P.getdata(),
                                                                  P.geometry.vcoordinate.position_on_grid)
            else:
                raise NotImplementedError("this vertical coordinate conversion.")

        return subdomain

###########
# pre-app #
###########
    @FileResource._openbeforedelayed
    def what(self, out=sys.stdout,
             details=False,
             sortfields=False,
             **_):
        """
        Writes in file a summary of the contents of the netCDFMNH file.

        :param out: the output open file-like object
        :param sortfields: **True** if the fields have to be sorted by type.
        """
        firstcolumn_width = 50
        secondcolumn_width = 16
        sepline = '{:-^{width}}'.format('', width=firstcolumn_width + secondcolumn_width + 1) + '\n'

        first_H2DField = [f for f in self.listfields() if inquire_field_dict(f)['type'] in ['H2D', '3D']][0]
        firstfield = self.readfield(first_H2DField, getdata=False)

        listfields = self.listfields()
        listoffields = listfields
        if sortfields:
            sortedfields = self.sortfields()

        def write_formatted(dest, label, value):
            dest.write('{:<{width}}'.format(label, width=firstcolumn_width) +
                       ':' +
                       '{:>{width}}'.format(str(value), width=secondcolumn_width) +
                       '\n')
        def write_formatted_col(dest, label, value):
            dest.write('{:>{width}}'.format(label, width=firstcolumn_width) +
                       ':' +
                       '{:>{width}}'.format(str(value), width=secondcolumn_width) +
                       '\n')
        def write_formatted_fields(dest, label, gridIndicator=None, comment=None):
            if gridIndicator is None and comment is None:
                dest.write('{:<{width}}'.format(label, width=20) +
                           '\n')
            else:
                dest.write('{:<{width}}'.format(label, width=20) +
                           ':' +
                           '{:^{width}}'.format(str(gridIndicator), width=10) +
                           ':' +
                           comment +
                           '\n')
        out.write("### FORMAT: " + self.format + "\n")
        out.write("\n")

        firstfield.what(out, vertical_geometry=False, fid=False)

        if self.geometry.vcoordinate is not None:
            self.geometry.vcoordinate.what(out, levels=False)

        out.write("######################\n")
        out.write("### LIST OF FIELDS ###\n")
        out.write("######################\n")
        if sortfields:
            listoffields = []
            for k in sorted(sortedfields.keys()):
                listoffields.append(k)
                listoffields.append('--------------------')
                listoffields.extend(sortedfields[k])
                listoffields.append('--------------------')
            numfields = sum([len(v) for v in sortedfields.values()])
        else:
            numfields = len(listoffields)
        out.write("Number: " + str(numfields) + "\n")
        if details is not None:
            write_formatted_fields(out, "Field name", "Grid ind.", "Comment")
        else:
            write_formatted_fields(out, "Field name")
        out.write(sepline)
        done = []
        for f in listoffields:
            if f not in done:
                if details:
                    field = self.readfield(f)
                    if hasattr(field, 'geometry'):
                        gridIndicator = {('__unknown__', '__unknown__'):0,
                                         ('center', 'mass'):1,
                                         ('center-left', 'mass'):2,
                                         ('lower-center', 'mass'):3,
                                         ('center', 'flux'):4,
                                         ('lower-left', 'mass'):5,
                                         ('center-left', 'flux'):6,
                                         ('lower-center', 'flux'):7,
                                         ('lower-left', 'flux'):8}[field.geometry.position_on_horizontal_grid,
                                                                   field.geometry.vcoordinate.position_on_grid]
                    else:
                        gridIndicator = '-'
                    write_formatted_fields(out, f, gridIndicator, field.comment)
                else:
                    write_formatted_fields(out, f)
                done.append(f)
        out.write(sepline)

# the netCDFMNH WAY #
###############
    @staticmethod
    def _get_latin1_latin2_lambert(lat0, rpk):
        def k(latin2):
            latin1 = lat0
            m1 = math.cos(math.radians(latin1))
            m2 = math.cos(math.radians(latin2))
            t1 = math.tan(math.pi / 4. - math.radians(latin1) / 2.)
            t2 = math.tan(math.pi / 4. - math.radians(latin2) / 2.)
            return (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2)) - rpk
        try:
            import scipy.optimize as op
            latin2 = Angle(op.fsolve(k, math.degrees(2 * math.asin(rpk)) - lat0)[0],
                           'degrees')
            latin1 = Angle(float(lat0), 'degrees')
        except Exception:
            def solve(function, x0):
                """A solver adapted to this problem. Do not try to use it elsewhere!"""
                x1 = x0 + 1.
                x2 = x0
                y1 = function(x1)
                y2 = function(x2)
                while math.fabs(y2) > 10.E-20 and math.fabs(y2) != math.fabs(y1):
                    x1, x2 = x2, x2 - (x2 - x1) / (y2 - y1) * y2
                    y1, y2 = y2, function(x2)
                return x2
            latin2 = Angle(solve(k, math.degrees(2 * math.asin(rpk)) - lat0),
                           'degrees')
            latin1 = Angle(float(lat0), 'degrees')
        return (latin1, latin2)

    @FileResource._openbeforedelayed
    def _read_geometry(self):
        """
        Reads the geometry in the netCDFMNH articles.
        Interface to Fortran routines from 'ifsaux'.
        """
        def v(name): return self._nc.variables[name][...]
        listnames = self._nc.variables.keys()
        
        if 'CARTESIAN' in listnames:
            cartesian = v('CARTESIAN')
        else:
            cartesian = False
        imax = int(v('IMAX'))
        jmax = int(v('JMAX'))
        xhat = v('XHAT')
        yhat = v('YHAT')
        if 'KMAX' in listnames:
            kmax = int(v('KMAX'))
            if kmax > 1:
                zhat = v('ZHAT')
                kmax += 2
        else:
            kmax = 0
        dimensions = {'X':imax + 2,
                      'Y':1 if (cartesian and jmax == 1) else jmax + 2,
                      'X_CIzone':imax,
                      'Y_CIzone':jmax,
                      'X_Iwidth':0,
                      'Y_Iwidth':0,
                      'X_Czone':imax,
                      'Y_Czone':jmax,
                      'X_CIoffset':1,
                      'Y_CIoffset':1
                      }

        if cartesian:
            lat0 = v('LAT0')
            lon0 = v('LON0')
            
            grid = {'X_resolution':xhat[1] - xhat[0],
                    'Y_resolution':yhat[1] - yhat[0],
                    'LAMzone':'CIE',
                    'latitude':Angle(float(lat0), 'degrees'),
                    'longitude':Angle(float(lon0), 'degrees'),
                    'input_lon':1,
                    'input_lat':1,
                    'input_position':(0, 0)
                    }
            projection = {'rotation':Angle(0., 'degrees'),
                          'reference_dX':grid['X_resolution'],
                          'reference_dY':grid['X_resolution']}
            geometryname = 'academic'
            kwargs_geom = dict(structure='3D',
                               name=geometryname,
                               grid=grid,
                               dimensions=dimensions,
                               projection=projection,
                               geoid=config.netCDFMNH_default_geoid,
                               )
        else:
            lat0 = v('LAT0')
            lon0 = v('LON0')
            lat1 = v('LATORI' if 'LATORI' in listnames else 'LATOR')
            lon1 = v('LONORI' if 'LONORI' in listnames else 'LONOR')
            rpk = v('RPK')
            beta = v('BETA')

            projection = {'reference_lon':Angle(float(lon0), 'degrees'),
                          'rotation': Angle(float(beta), 'degrees')
                          }
            if abs(rpk - math.sin(math.radians(lat0))) <= config.epsilon:
                # non secant
                projection['reference_lat'] = Angle(float(lat0), 'degrees')
            else:
                if abs(rpk) in [0., 1.]:
                    # mercator or polar stereographic: one secant latitude
                    projection['reference_lat'] = Angle(float(numpy.copysign(90, lat0)), 'degrees')
                    projection['secant_lat'] = Angle(float(lat0), 'degrees')
                else:
                    # lambert: two secant latitudes
                    latin1, latin2 = self._get_latin1_latin2_lambert(lat0, rpk)
                    projection['secant_lat1'] = latin1
                    projection['secant_lat2'] = latin2
            grid = {'X_resolution':xhat[1] - xhat[0],
                    'Y_resolution':yhat[1] - yhat[0],
                    'LAMzone':'CIE',
                    'input_lon':Angle(float(lon1), 'degrees'),
                    'input_lat':Angle(float(lat1), 'degrees'),
                    'input_position':(0, 0),
                    }
            if abs(rpk) <= config.epsilon:
                geometryname = 'mercator'
            elif abs(1 - abs(rpk)) <= config.epsilon:
                geometryname = 'polar_stereographic'
            else:
                geometryname = 'lambert'

            kwargs_geom = dict(structure='3D',
                               name=geometryname,
                               grid=grid,
                               dimensions=dimensions,
                               geoid=config.netCDFMNH_default_geoid,
                               projection=projection
                               )

        if kmax > 1:
            H = zhat[-1]
            if 'SLEVE' in listnames:
                sleve = v('SLEVE')
            else:
                sleve = False
            Ai = [c for c in zhat[0:kmax + 2]][1:]
            Bi = [1 - c / H for c in zhat[0:kmax + 2]][1:]
            grid = {'gridlevels': tuple([(i + 1, FPDict({'Ai':Ai[i], 'Bi':Bi[i]})) for
                                         i in range(len(Ai))]),
                    'ABgrid_position':'flux'}
            kwargs_vcoord = {'structure': 'V',
                             'typeoffirstfixedsurface':118 if not sleve else 255,
                             'position_on_grid': 'mass',
                             'grid': grid,
                             'levels': list([i for i in range(len(Ai) + 1)])
                             }
        else:
            kwargs_vcoord = {'structure': 'V',
                             'typeoffirstfixedsurface': 255,
                             'position_on_grid': '__unknown__',
                             'levels':[255]}
        kwargs_geom['position_on_horizontal_grid'] = 'center'
        kwargs_geom['vcoordinate'] = fpx.geometry(**kwargs_vcoord)
        self.geometry = fpx.geometry(**kwargs_geom)

    @FileResource._openbeforedelayed
    def _read_validity(self):
        """Reads the validity in the netCDFMNH articles."""
        listnames = self._nc.variables.keys()
        def todate(fieldname):
            var = self._nc.variables[fieldname]
            if var.units.startswith("seconds since ") and \
               var.units.endswith(" +0:00"):
                value = datetime.datetime.strptime(var.units, "seconds since %Y-%m-%d %H:%M:%S +0:00")
                value += datetime.timedelta(seconds=float(var[...]))
            else:
                raise epygramError("Unknown unit for time: " + var.units)
            return value
        kwargs = {}
        if 'DTEXP' in listnames:
            kwargs['basis'] = todate('DTEXP')
            if 'DTCUR' in listnames:
                kwargs['term'] = todate('DTCUR') - kwargs['basis']
        elif 'DTCUR' in listnames:
            kwargs['date_time'] = todate('DTCUR')
        kwargs['cumulativeduration'] = datetime.timedelta(seconds=0)
        self.validity = FieldValidity(**kwargs)

    def _get_geometryValidity_from_field(self, field):
        """
        Returns special record needed to represent the geometry
        and the validty of this field.
        """
        specialFields = dict()
        g = field.geometry
        field_info = inquire_field_dict(field.fid[self.format])
        specialFields['IMAX'] = g.dimensions['X'] - 2
        specialFields['JMAX'] = 1 if g.dimensions['Y'] == 1 else g.dimensions['Y'] - 2
        dimX = g.dimensions['X']
        if dimX == 1:
            dimX += 2
        specialFields['XHAT'] = numpy.arange(-g.grid['X_resolution'] / 2.,
                                             g.grid['X_resolution'] * (dimX - 1),
                                             g.grid['X_resolution'])
        dimY = g.dimensions['Y']
        if dimY == 1:
            dimY += 2
        specialFields['YHAT'] = numpy.arange(-g.grid['Y_resolution'] / 2.,
                                             g.grid['Y_resolution'] * (dimY - 1),
                                             g.grid['Y_resolution'])
        if g.vcoordinate is not None:
            if field_info['type'] == '3D':
                specialFields['SLEVE'] = g.vcoordinate.typeoffirstfixedsurface != 118
                kmax = len(g.vcoordinate.grid['gridlevels']) + 1
                if kmax > 1:
                    kmax -= 2
                specialFields['KMAX'] = kmax
                Ai = [level[1]['Ai'] for level in g.vcoordinate.grid['gridlevels']]
                Ai = [-Ai[1]] + Ai
                specialFields['ZHAT'] = Ai
                if g.vcoordinate.grid['ABgrid_position'] != 'flux':
                    raise epygramError("Don't jnow how to deal with ABgrid_position!='flux'")
        if g.name == 'academic':
            specialFields['BETA'] = 0.
            specialFields['CARTESIAN'] = True
            if 'latitude' in g.grid:
                specialFields['LAT0'] = g.grid['latitude'].get('degrees')
            else:
                specialFields['LAT0'] = 0.
            if 'longitude' in g.grid:
                specialFields['LON0'] = g.grid['longitude'].get('degrees')
            else:
                specialFields['LON0'] = 0.
        else:
            specialFields['BETA'] = g.projection['rotation'].get('degrees')
            specialFields['CARTESIAN'] = False
            specialFields['LON0'] = g.projection['reference_lon'].get('degrees')
            if not g.secant_projection:
                specialFields['LAT0'] = g.projection['reference_lat'].get('degrees')
                specialFields['RPK'] = math.sin(g.projection['reference_lat'].get('radians'))
            else:
                if g.name in ['mercator', 'polar_stereographic']:
                    specialFields['LAT0'] = g.projection['secant_lat'].get('degrees')
                    if g.name == 'mercator':
                        specialFields['RPK'] = 0.
                    else:
                        specialFields['RPK'] = numpy.copysign(1, specialFields['LAT0'])
                else:
                    latin1 = g.projection['secant_lat1'].get('degrees')
                    latin2 = g.projection['secant_lat2'].get('degrees')
                    m1 = math.cos(math.radians(latin1))
                    m2 = math.cos(math.radians(latin2))
                    t1 = math.tan(math.pi / 4. - math.radians(latin1) / 2.)
                    t2 = math.tan(math.pi / 4. - math.radians(latin2) / 2.)
                    specialFields['LAT0'] = latin1
                    specialFields['RPK'] = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2))
            if g.grid['input_position'] == (0, 0):
                specialFields['LONOR'] = g.grid['input_lon'].get('degrees')
                specialFields['LATOR'] = g.grid['input_lat'].get('degrees')
            else:
                originPoint = g.gimme_corners_ll(subzone='CIE', position='center')['ll']
                specialFields['LONOR'] = originPoint[0]
                specialFields['LATOR'] = originPoint[1]
            specialFields['LONORI'] = specialFields['LONOR']
            specialFields['LATORI'] = specialFields['LATOR']

        if field.validity is not None:
            if len(field.validity) != 1:
                raise epygramError("netCDFMNH can hold only one validity.")
            basis = field.validity.getbasis()
            if basis is not None:
                specialFields['DTEXP%TDATE'] = numpy.array([basis.year, basis.month, basis.day], dtype=numpy.int64)
                specialFields['DTEXP%TIME'] = float(basis.hour * 3600 + basis.minute * 60 + basis.second)
                validity = field.validity.get()
                if validity is not None:
                    specialFields['DTCUR%TDATE'] = numpy.array([validity.year, validity.month, validity.day], dtype=numpy.int64)
                    specialFields['DTCUR%TIME'] = float(validity.hour * 3600 + validity.minute * 60 + validity.second)
        return specialFields

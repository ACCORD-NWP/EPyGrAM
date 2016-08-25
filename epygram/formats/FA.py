#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
Contains the class for FA format.

Plus a function to guess a field type given its name.
"""

__all__ = ['FA', 'inquire_field_dict']

import datetime
import os
import copy
import numpy
import math
import re
import sys

import footprints
from footprints import FPDict, FPList, proxy as fpx
from arpifs4py import wfa, wlfi, wtransforms

from epygram import config, epygramError, util
from epygram.util import Angle, separation_line, \
                         write_formatted_fields
from epygram.base import FieldSet, FieldValidity, FieldValidityList
from epygram.resources import FileResource
from epygram.geometries import D3Geometry, SpectralGeometry
from epygram.geometries.VGeometry import hybridP2pressure, hybridP2altitude, \
                                         pressure2altitude
from epygram.fields import MiscField, H2DField

epylog = footprints.loggers.getLogger(__name__)



def inquire_field_dict(fieldname):
    """
    Returns the info contained in the FA _field_dict for the requested field.
    """
    if FA._field_dict == []:
        FA._read_field_dict(FA.CSV_field_dictionaries['default'])
        if os.path.exists(FA.CSV_field_dictionaries['user']):
            FA._read_field_dict(FA.CSV_field_dictionaries['user'])

    matching_field = None
    for fd in FA._field_dict:
        dictitem = fd['name']
        pattern = re.subn('\.', r'\.', dictitem)[0]  # protect '.'
        pattern = pattern.replace('?', '.')  # change unix '?' to python '.'
        pattern = pattern.replace('*', '.*')  # change unix '*' to python '.*'
        pattern += '(?!.)'
        if re.match(pattern, fieldname):
            matching_field = fd
            break

    if matching_field == None:
        epylog.info("field '" + fieldname + "' is not referenced in" + \
                    " Field_Dict_FA. Assume its type being a MiscField.")
        matching_field = {'name':fieldname, 'type':'Misc',
                          'nature':'float', 'dimension':'1'}

    return copy.deepcopy(matching_field)

def _complete_generic_fid_from_name(generic_fid, fieldname):
    """Complete a generic fid with information of fieldname."""

    # 'level'
    if 'level' not in generic_fid:
        if generic_fid['typeOfFirstFixedSurface'] == 119:  # hybrid-pressure
            generic_fid['level'] = int(fieldname[1:4])
        elif generic_fid['typeOfFirstFixedSurface'] == 100:  # isobaric (P)
            level = int(fieldname[1:6])
            if level == 0:  # problem linked to number of digits
                level = 100000
            generic_fid['level'] = level / 100  # hPa
        elif generic_fid['typeOfFirstFixedSurface'] == 103:  # height
            try:
                generic_fid['level'] = int(fieldname[1:6])
            except ValueError:
                generic_fid['level'] = 255
        elif generic_fid['typeOfFirstFixedSurface'] == 109:  # PV
            generic_fid['level'] = float(fieldname[1:4]) / 10.  # to handle PVU
        elif generic_fid['typeOfFirstFixedSurface'] == 20:  # T
            if fieldname[0:2] == 'KT':
                generic_fid['level'] = int(fieldname[2:5])
            elif fieldname[0:2] == 'T':
                generic_fid['level'] = int(fieldname[1:4])
        else:
            generic_fid['level'] = 0
    # ISP
    if fieldname[0] == 'C':
        if any([sat in fieldname for sat in ['METEOSAT', 'GOES', 'MTSAT']]):
            channel = int(fieldname[1:4])
            # C{ccc}_METEOSAT_{ss}
            sat = 'METEOSAT'
            if sat in fieldname:
                satnum = int(fieldname.strip()[-2:])
                satellite = sat + str(satnum)
                if satnum <= 7:
                    sensor = 'MVIRI'
                else:
                    sensor = 'SEVIRI'
            # C{ccc}_GOES_{ss}_IMA
            sat = 'GOES'
            if sat in fieldname:
                satnum = int(fieldname.strip()[10:12])
                satellite = sat + str(satnum)
                sensor = 'IMAGER'
            # C{ccc}_GOES_{ss}_IMA
            sat = 'MTSAT'
            if sat in fieldname:
                satnum = int(fieldname.strip()[11:13])
                satellite = sat + str(satnum)
                sensor = 'IMAGER'
            # category=satellite, number=sensor, level=channel
            generic_fid['parameterCategory'] = config.satellites_local_GRIB2[satellite]
            generic_fid['parameterNumber'] = config.sensors_local_GRIB2[sensor]
            generic_fid['level'] = channel

    return generic_fid

def get_generic_fid(fieldname):
    """Return a generic fid from fieldname (via Field Dict)."""

    fid = inquire_field_dict(fieldname)
    fid = _complete_generic_fid_from_name(fid, fieldname)
    fid.pop('type')
    fid.pop('name')

    return fid

def _gen_headername():
    """Generates a random headername for the FA software."""
    import uuid

    return str(uuid.uuid4()).replace('-', '')[0:16]

def _create_header_from_geometry(geometry, spectral_geometry=None):
    """
    Create a header and returns its name, from a geometry and (preferably)
    a SpectralGeometry.
    Args:
    - geometry: a D3Geometry (or heirs) instance, from which the header is set.
    - spectral_geometry: optional, a SpectralGeometry instance, from which
      truncation is set in header. If not provided, in LAM case the X/Y
      truncations are computed from field dimension, as linear grid.
      In global case, an error is raised.
    Contains a call to arpifs4py.wfa.wfacade (wrapper for FACADE routine).
    """

    assert isinstance(geometry, D3Geometry), \
           "geometry must be a D3Geometry (or heirs) instance."
    if spectral_geometry != None and\
       not isinstance(spectral_geometry, SpectralGeometry):
        raise epygramError("spectral_geometry must be a SpectralGeometry" + \
                           " instance.")
    if geometry.projected_geometry:
        assert geometry.projection['rotation'] == Angle(0., 'radians'), \
               "the geometry's projection attribute 'rotation' must be 0. in FA."

    headername = _gen_headername()
    CDNOMC = headername

    JPXPAH = FA._FAsoftware_cst['JPXPAH']
    JPXIND = FA._FAsoftware_cst['JPXIND']
    if geometry.rectangular_grid:
        if spectral_geometry is not None:
            truncation_in_X = spectral_geometry.truncation['in_X']
            truncation_in_Y = spectral_geometry.truncation['in_Y']
        else:
            # default: linear truncation...
            truncation_in_X = numpy.floor((geometry.dimensions['X'] - 1) / 2).astype('int')
            truncation_in_Y = numpy.floor((geometry.dimensions['Y'] - 1) / 2).astype('int')

        # scalars
        if geometry.name == 'regular_lonlat':
            KTYPTR = -11
        else:
            KTYPTR = -1 * truncation_in_X
        if geometry.name in ('lambert', 'polar_stereographic'):
            PSLAPO = geometry.getcenter()[0].get('radians') - \
                     geometry.projection['reference_lon'].get('radians')
        else:
            PSLAPO = 0.0
        PCLOPO = 0.0
        PSLOPO = 0.0
        if geometry.name == 'academic':
            PCODIL = -1.0
        else:
            PCODIL = 0.0
        if geometry.name == 'regular_lonlat':
            KTRONC = 11
        else:
            KTRONC = truncation_in_Y
        KNLATI = geometry.dimensions['Y']
        KNXLON = geometry.dimensions['X']

        # KNLOPA
        KNLOPA = numpy.zeros(JPXPAH, dtype=numpy.int64)
        KNLOPA[0] = max(0, min(11, truncation_in_X, truncation_in_Y) - 1)
        if geometry.name == 'regular_lonlat':
            corners = geometry.gimme_corners_ij()
            KNLOPA[1] = 0
            KNLOPA[2] = 1 + corners['ll'][0]
            KNLOPA[3] = 1 + corners['ur'][0]
            KNLOPA[4] = 1 + corners['ll'][1]
            KNLOPA[5] = 1 + corners['ur'][1]
            KNLOPA[6] = 8
            KNLOPA[7] = 8
        else:
            corners = geometry.gimme_corners_ij(subzone='CI')
            if geometry.grid['LAMzone'] == 'CIE':
                KNLOPA[1] = 1
            KNLOPA[2] = 1 + corners['ll'][0]
            KNLOPA[3] = 1 + corners['ur'][0]
            KNLOPA[4] = 1 + corners['ll'][1]
            KNLOPA[5] = 1 + corners['ur'][1]
            KNLOPA[6] = geometry.dimensions['X_Iwidth']
            KNLOPA[7] = geometry.dimensions['Y_Iwidth']

        KNOZPA = numpy.zeros(JPXIND, dtype=numpy.int64)

        # PSINLA
        PSINLA = numpy.zeros(max((1 + geometry.dimensions['Y']) / 2, 18))
        PSINLA[0] = -1
        if geometry.name == 'regular_lonlat':
            PSINLA[1] = -9
            PSINLA[2] = geometry.getcenter()[0].get('radians')
            PSINLA[3] = geometry.getcenter()[1].get('radians')
            PSINLA[4] = geometry.getcenter()[0].get('radians')
            PSINLA[5] = geometry.getcenter()[1].get('radians')
            PSINLA[6] = geometry.grid['X_resolution'].get('radians')
            PSINLA[7] = geometry.grid['Y_resolution'].get('radians')
            PSINLA[8] = geometry.grid['X_resolution'].get('radians') * geometry.dimensions['X']
            PSINLA[9] = geometry.grid['Y_resolution'].get('radians') * geometry.dimensions['Y']
            PSINLA[10] = 0.0
            PSINLA[11] = 0.0
        elif geometry.projected_geometry:
            if geometry.secant_projection:
                raise epygramError("cannot write secant projected" + \
                                   " geometries in FA.")
            PSINLA[1] = geometry.projection['reference_lat'].get('cos_sin')[1]
            PSINLA[6] = geometry.grid['X_resolution']
            PSINLA[7] = geometry.grid['Y_resolution']
            PSINLA[8] = geometry.grid['X_resolution'] * geometry.dimensions['X']
            PSINLA[9] = geometry.grid['Y_resolution'] * geometry.dimensions['Y']
            PSINLA[10] = 2 * math.pi / PSINLA[8]
            PSINLA[11] = 2 * math.pi / PSINLA[9]
            PSINLA[2] = geometry.projection['reference_lon'].get('radians')
            PSINLA[3] = geometry.projection['reference_lat'].get('radians')
            PSINLA[4] = geometry.getcenter()[0].get('radians')
            PSINLA[5] = geometry.getcenter()[1].get('radians')
        if geometry.name != 'academic':
            PSINLA[12] = Angle(geometry.ij2ll(*corners['ll'])[0], 'degrees').get('radians')
            PSINLA[13] = Angle(geometry.ij2ll(*corners['ll'])[1], 'degrees').get('radians')
            PSINLA[14] = Angle(geometry.ij2ll(*corners['ur'])[0], 'degrees').get('radians')
            PSINLA[15] = Angle(geometry.ij2ll(*corners['ur'])[1], 'degrees').get('radians')
        else:
            PSINLA[6] = geometry.grid['X_resolution']
            PSINLA[7] = geometry.grid['Y_resolution']
            PSINLA[8] = geometry.grid['X_resolution'] * geometry.dimensions['X']
            PSINLA[9] = geometry.grid['Y_resolution'] * geometry.dimensions['Y']
            PSINLA[10] = 2 * math.pi / PSINLA[8]
            PSINLA[11] = 2 * math.pi / PSINLA[9]

    else:  # global
        if geometry.name == 'reduced_gauss':
            KTYPTR = 1
            PSLAPO = 0.  # as observed in files; why not 1. ?
            PCLOPO = 0.  # as observed in files; why not 1. ?
            PSLOPO = 0.
        elif geometry.name == 'rotated_reduced_gauss':
            KTYPTR = 2
            PSLAPO = geometry.grid['pole_lat'].get('cos_sin')[1]
            PCLOPO = geometry.grid['pole_lon'].get('cos_sin')[0]
            PSLOPO = geometry.grid['pole_lon'].get('cos_sin')[1]
        PCODIL = geometry.grid['dilatation_coef']
        if spectral_geometry is not None:
            KTRONC = spectral_geometry.truncation['max']
        else:
            # default: linear truncation...
            KTRONC = (geometry.dimensions['max_lon_number'] - 1) / 2
            KTRONC = 2 * (KTRONC // 2)  # make it even
        KNLATI = geometry.dimensions['lat_number']
        KNXLON = geometry.dimensions['max_lon_number']
        KNLOPA = numpy.zeros(JPXPAH, dtype=numpy.int64)
        KNLOPA[0:KNLATI / 2] = geometry.dimensions['lon_number_by_lat'][0:KNLATI / 2]
        KNOZPA = numpy.zeros(JPXIND, dtype=numpy.int64)
        if spectral_geometry is not None:
            KNOZPA[0:KNLATI / 2] = spectral_geometry.truncation['max_zonal_wavenumber_by_lat'][0:KNLATI / 2]
        else:
            KNOZPA[0:KNLATI / 2] = wtransforms.w_trans_inq(geometry.dimensions['lat_number'],
                                                           KTRONC,
                                                           len(geometry.dimensions['lon_number_by_lat']),
                                                           numpy.array(geometry.dimensions['lon_number_by_lat']),
                                                           config.KNUMMAXRESOL)[2][0:KNLATI / 2]
        PSINLA = numpy.zeros((1 + geometry.dimensions['lat_number']) / 2,
                             dtype=numpy.float64)
        PSINLA[0:KNLATI / 2] = numpy.array([s.get('cos_sin')[1] for s in
                                            geometry.grid['latitudes'][0:KNLATI / 2]])

    # vertical geometry
    PREFER = FA.reference_pressure
    if geometry.vcoordinate.grid is not None and geometry.vcoordinate.grid != {}:
        Ai = [level[1]['Ai'] for level in geometry.vcoordinate.grid['gridlevels']]
        Bi = [level[1]['Bi'] for level in geometry.vcoordinate.grid['gridlevels']]
        KNIVER = len(Ai) - 1
        PAHYBR = numpy.array(Ai) / FA.reference_pressure
        PBHYBR = numpy.array(Bi)
    else:
        KNIVER = 1
        PAHYBR = numpy.array([0., 0.])
        PBHYBR = numpy.array([0., 1.])
    LDGARD = True

    wfa.wfacade(CDNOMC,
                KTYPTR, PSLAPO, PCLOPO, PSLOPO,
                PCODIL, KTRONC,
                KNLATI, KNXLON,
                len(KNLOPA), KNLOPA,
                len(KNOZPA), KNOZPA,
                len(PSINLA), PSINLA,
                KNIVER, PREFER, PAHYBR, PBHYBR,
                LDGARD)

    return headername

class FA(FileResource):
    """Class implementing all specificities for FA resource format."""

    _footprint = dict(
        attr=dict(
            format=dict(
                values=set(['FA']),
                default='FA'),
            headername=dict(
                optional=True,
                info="With openmode == 'w', name of an existing header," + \
                     " for the new FA to use its geometry."),
            validity=dict(
                type=FieldValidityList,
                optional=True,
                access='rwx',
                info="With openmode == 'w', describes the temporal validity" + \
                      " of the resource."),
            default_compression=dict(
                type=FPDict,
                optional=True,
                info="Default compression for writing fields in resource."),
            cdiden=dict(
                optional=True,
                default='unknown',
                access='rwx',
                info="With openmode == 'w', identifies the FA by a keyword," + \
                     " usually the model abbreviation."),
            processtype=dict(
                optional=True,
                default='analysis',
                access='rwx',
                info="With openmode == 'w', identifies the processus that" + \
                     " produced the resource.")
        )
    )

    # the Field Dictionary gathers info about fields nature
    CSV_field_dictionaries = config.FA_field_dictionaries_csv
    # syntax: _field_dict = [{'name':'fieldname1', 'type':'...', ...},
    #                        {'name':'fieldname2', 'type':'...', ...}, ...]
    _field_dict = []
    # reference pressure coefficient for converting hybrid A coefficients in FA
    reference_pressure = config.FA_default_reference_pressure

    @classmethod
    def _FAsoft_init(cls):
        """Initialize the FA software maximum dimensions."""
        cls._FAsoftware_cst = dict(zip(('JPXPAH', 'JPXIND', 'JPXGEO', 'JPXNIV'),
                                       wfa.get_facst()))

    @classmethod
    def _read_field_dict(cls, fd_abspath):
        """Reads the CSV fields dictionary of the format."""

        field_dict, file_priority = util.read_CSV_as_dict(fd_abspath)

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
        """Constructor. See its footprint for arguments."""

        self.isopen = False

        # At creation of the first FA, initialize FA._field_dict
        if self._field_dict == []:
            self._read_field_dict(self.CSV_field_dictionaries['default'])
            if os.path.exists(self.CSV_field_dictionaries['user']):
                self._read_field_dict(self.CSV_field_dictionaries['user'])

        super(FA, self).__init__(*args, **kwargs)

        # Initialization of FA software (if necessary):
        if not hasattr(self, '_FAsoftware_cst'):
            self._FAsoft_init()
        self.fieldscompression = {}

        if not self.fmtdelayedopen:
            self.open()

    def open(self, geometry=None, spectral_geometry=None, validity=None,
             openmode=None):
        """
        Opens a FA with ifsaux' FAITOU, and initializes some attributes.

        Actually, as FAITOU needs an existing header with 'w' *openmode*,
        opening file in 'w' *openmode* will require else an existing header to
        which the resource is linked, or to create a header from a geometry via
        *_create_header_from_geometry()* function. This explains the eventual
        need for *geometry/spectral_geometry/validity* in *open()* method. If
        neither headername nor geometry are available, resource is not opened:
        the *open()* will be called again at first writing of a field in
        resource.

        Args: \n
        - *geometry*: optional, must be a
          :class:`epygram.geometries.D3Geometry` (or heirs) instance.
        - *spectral_geometry*: optional, must be a
          :class:`epygram.geometries.SpectralGeometry` instance.
        - *validity*: optional, must be a :class:`epygram.base.FieldValidity` or
          a :class:`epygram.base.FieldValidityList` instance.
        - *openmode*: optional, to open with a specific openmode, eventually
          different from the one specified at initialization.
        """

        super(FA, self).open(openmode=openmode)

        if self.openmode in ('r', 'a'):
            # FA already exists, including geometry and validity
            if self.openmode in ('r', 'a') and self.headername == None:
                self._attributes['headername'] = _gen_headername()
            if geometry != None or validity != None:
                epylog.warning(self.container.abspath + ": FA.open():" + \
                               " geometry/validity argument will be ignored" + \
                               " with this openmode ('r','a').")
            # open, getting logical unit
            try:
                self._unit = wfa.wfaitou(self.container.abspath,
                                         'OLD',
                                         self.headername)
            except RuntimeError as e:
                raise IOError(e)
            self.isopen = True
            self.empty = False
            # read info
            self._attributes['cdiden'] = wfa.wfalsif(self._unit)
            self._read_geometry()
            self._read_validity()
            if self.openmode == 'a':
                if self.default_compression == None:
                    self._attributes['default_compression'] = self._getrunningcompression()
                self._setrunningcompression(**self.default_compression)
        elif self.openmode == 'w':
            if geometry != None:
                if not isinstance(geometry, D3Geometry):
                    raise epygramError("geometry must be a D3Geometry (or heirs) instance.")
                self._attributes['headername'] = _create_header_from_geometry(geometry, spectral_geometry)
            if validity != None:
                if (not isinstance(validity, FieldValidity)) and (not isinstance(validity, FieldValidityList)):
                    raise epygramError("validity must be a FieldValidity or FieldValidityList instance.")
                if isinstance(validity, FieldValidityList) and len(validity) != 1:
                    raise epygramError("FA can hold only one validity.")
                self._attributes['validity'] = validity
            if self.headername != None and self.validity != None:
                # new FA, with an already existing header and validity
                # set geometry from existing header
                self._read_geometry()
                # open
                if os.path.exists(self.container.abspath):
                    if config.protect_unhappy_writes and not self._overwrite:
                        raise IOError('This file already exist: ' + self.container.abspath)
                    else:
                        os.remove(self.container.abspath)
                (self._unit) = wfa.wfaitou(self.container.abspath,
                                           'NEW',
                                           self.headername)
                self.isopen = True
                self.empty = True
                # set FA date
                if self.validity.get() != self.validity.getbasis():
                    self.processtype = 'forecast'
                self._set_validity()
                # set CDIDEN
                wfa.wfautif(self._unit, self.cdiden)
                if self.default_compression == None:
                    self._attributes['default_compression'] = config.FA_default_compression
                self._setrunningcompression(**self.default_compression)
            elif self.headername == None or self.validity == None:
                # a header need to be created prior to opening:
                # header definition (then opening) will be done from the
                # geometry taken in the first field to be written in resource
                pass

    def close(self):
        """Closes a FA with ifsaux' FAIRME."""

        if self.isopen:
            try:
                wfa.wfairme(self._unit, 'KEEP')
            except Exception:
                raise IOError("closing " + self.container.abspath)
            self.isopen = False
            # Cleanings
            #if self.openmode == 'w' and self.empty:
            #    os.remove(self.container.abspath)



################
# ABOUT FIELDS #
################

    def find_fields_in_resource(self, seed=None, fieldtype=[], generic=False):
        """
        Returns a list of the fields from resource whose name match the given
        seed.

        Args: \n
        - *seed*: might be a regular expression, a list of regular expressions
          or *None*. If *None* (default), returns the list of all fields in
          resource.
        - *fieldtype*: optional, among ('H2D', 'Misc') or a list of these strings.
          If provided, filters out the fields not of the given types.
        - *generic*: if True, returns complete fid's,
          union of {'FORMATname':fieldname} and the according generic fid of
          the fields.
        """

        if type(fieldtype) == type(list()):
            fieldtypeslist = fieldtype
        else:
            fieldtypeslist = [fieldtype]
        fieldslist = []
        if seed == None:
            tmplist = self.listfields()
            for f in tmplist:
                if fieldtypeslist == [] or\
                   inquire_field_dict(f)['type'] in fieldtypeslist:
                    fieldslist.append(f)
        elif isinstance(seed, str):
            tmplist = util.find_re_in_list(seed, self.listfields())
            for f in tmplist:
                if fieldtypeslist == [] or\
                   inquire_field_dict(f)['type'] in fieldtypeslist:
                    fieldslist.append(f)
        elif isinstance(seed, list):
            tmplist = []
            for s in seed:
                tmplist += util.find_re_in_list(s, self.listfields())
            for f in tmplist:
                if fieldtypeslist == [] or\
                   inquire_field_dict(f)['type'] in fieldtypeslist:
                    fieldslist.append(f)
        if fieldslist == []:
            raise epygramError("no field matching: " + str(seed) + \
                               " was found in resource " + \
                               self.container.abspath)
        if generic:
            fieldslist = [(f, get_generic_fid(f)) for f in fieldslist]

        return fieldslist

    def listfields(self, **kwargs):
        """
        Returns a list containing the FA identifiers of all the fields of the
        resource.
        """
        return super(FA, self).listfields(**kwargs)

    @FileResource._openbeforedelayed
    def _listfields(self, complete=False):
        """
        Actual listfields() method for FA.
        
        Args: \n
        - *complete*: if True method returns a list of {'FA':FA_fid, 'generic':generic_fid}
                      if False method return a list of FA_fid
        """

        records_number = wlfi.wlfinaf(self._unit)[0]
        wlfi.wlfipos(self._unit)  # rewind
        fieldslist = []
        for i in range(records_number):
            fieldname = wlfi.wlficas(self._unit, True)[0]
            if i >= 7 and fieldname != 'DATX-DES-DONNEES':
                fieldslist.append(fieldname.strip())
            # i >= 7: 7 first fields in LFI are the header ("cadre")
            # 8th field added by P.Marguinaud, DATX-DES-DONNEES, to store dates
            # with 1-second precision, from cy40t1 onwards.

        if complete:
            return [{'FA':f, 'generic':get_generic_fid(f)} for f in fieldslist]
        else:
            return fieldslist

    def split_UV(self, fieldseed):
        """
        Return two lists of fids corresponding respectively to U and V
        components of wind, given a *fieldseed*.
        """

        fids = self.find_fields_in_resource(fieldseed + '*')
        if fieldseed.startswith('S'):
            Ufid = [f for f in fids if 'WIND.U.PHYS' in f]
            Vfid = [f for f in fids if 'WIND.V.PHYS' in f]
        elif fieldseed[0] in ('P', 'H', 'V'):
            Ufid = [f for f in fids if 'VENT_ZONAL' in f]
            Vfid = [f for f in fids if 'VENT_MERID' in f]
        elif fieldseed.startswith('CLS') and 'VENT' in fids[0]:
            if fieldseed.startswith('CLSVENTNEUTRE'):
                Ufid = [f for f in fids if 'CLSVENTNEUTRE.U' in f]
                Vfid = [f for f in fids if 'CLSVENTNEUTRE.V' in f]
            else:
                Ufid = [f for f in fids if 'VENT.ZONAL' in f]
                Vfid = [f for f in fids if 'VENT.MERIDIEN' in f]
        elif fieldseed.startswith('CLS') and 'RAF' in fids[0]:
            Ufid = [f for f in fids if 'CLSU' in f]
            Vfid = [f for f in fids if 'CLSV' in f]
        else:
            raise NotImplementedError("split_UV: field syntax='" + fieldseed + "'")

        return (sorted(Ufid), sorted(Vfid))

    def sortfields(self):
        """
        Returns a sorted list of fields with regards to their name and nature,
        as a dict of lists.
        """

        list3Dparams = []
        list3D = []
        list2D = []

        # final lists
        list3Dsp = []
        list3Dgp = []
        list2Dsp = []
        list2Dgp = []
        listMisc = []

        for f in self.listfields():
            info = inquire_field_dict(f)
            # separate H2D from Misc
            if info['type'] == 'H2D':
                # separate 3D from 2D
                if info['typeOfFirstFixedSurface'] in ('119', '100', '103',
                                                       '109', '20'):
                    list3D.append(f)
                    list3Dparams.append(filter(lambda x: not x.isdigit(),
                                               f[1:]))
                else:
                    list2D.append(f)
            else:
                listMisc.append(f)
        list3Dparams = list(set(list3Dparams))
        # separate gp/sp
        for f in list2D:
            if self.fieldencoding(f)['spectral']:
                list2Dsp.append(f)
            else:
                list2Dgp.append(f)
        # sort 2D
        list2Dsp.sort()
        list2Dgp.sort()
        # sort parameters
        for p in sorted(list3Dparams):
            interlist = []
            for f in list3D:
                if p in f:
                    interlist.append(f)
            # sort by increasing level
            interlist.sort()
            # separate gp/sp
            if self.fieldencoding(interlist[0])['spectral']:
                list3Dsp.extend(interlist)
            else:
                list3Dgp.extend(interlist)
        outlists = {'3D spectral fields':list3Dsp,
                    '3D gridpoint fields':list3Dgp,
                    '2D spectral fields':list2Dsp,
                    '2D gridpoint fields':list2Dgp,
                    'Misc-fields':listMisc}

        return outlists

    @FileResource._openbeforedelayed
    def fieldencoding(self, fieldname):
        """
        Returns a dict containing info about how the field is encoded:
        spectral? and compression. Interface to ifsaux' FANION.
        """

        (LDCOSP, KNGRIB, KNBITS, KSTRON, KPUILA) = wfa.wfanion(self._unit,
                                                               fieldname[0:4],
                                                               0,
                                                               fieldname[4:])[1:6]
        encoding = {'spectral':LDCOSP, 'KNGRIB':KNGRIB, 'KNBITS':KNBITS,
                    'KSTRON':KSTRON, 'KPUILA':KPUILA}

        return encoding

    @FileResource._openbeforedelayed
    def readfield(self, fieldname,
                  getdata=True,
                  footprints_builder=config.use_footprints_as_builder):
        """
        Reads one field, given its FA name, and returns a Field instance.
        Interface to Fortran routines from 'ifsaux'.
        
        Args: \n
        - *fieldname*: FA fieldname
        - *getdata*: if *False*, only metadata are read, the field do not
          contain data.
        - *footprints_builder*: if *True*, uses footprints.proxy to build
          fields. Defaults to False for performance reasons.
        """

        if self.openmode == 'w':
            raise epygramError("cannot read fields in resource if with" + \
                               " openmode == 'w'.")
        assert fieldname in self.listfields(), ' '.join(["field",
                                                         str(fieldname),
                                                         "not found in resource."])
        # Get field info
        field_info = inquire_field_dict(fieldname)
        if footprints_builder:
            builder = fpx.field
        else:
            if field_info['type'] == 'H2D':
                builder = H2DField
            elif field_info['type'] == 'Misc':
                builder = MiscField
        if field_info['type'] == 'H2D':
            encoding = self.fieldencoding(fieldname)
            # Save compression in FA
            compression = {'KNGRIB':encoding['KNGRIB'],
                           'KNBPDG':encoding['KNBITS'],
                           'KNBCSP':encoding['KNBITS'],
                           'KSTRON':encoding['KSTRON'],
                           'KPUILA':encoding['KPUILA']}
            self.fieldscompression[fieldname] = compression

            # vertical geometry
            kwargs_vcoord = {'structure': 'V',
                             'typeoffirstfixedsurface': self.geometry.vcoordinate.typeoffirstfixedsurface,
                             'position_on_grid': self.geometry.vcoordinate.position_on_grid,
                             'grid': self.geometry.vcoordinate.grid,
                             'levels': self.geometry.vcoordinate.levels}
            field_info = _complete_generic_fid_from_name(field_info, fieldname)
            for k in field_info:
                if k == 'typeOfFirstFixedSurface':
                    kwargs_vcoord['typeoffirstfixedsurface'] = field_info[k]
                elif k == 'level':
                    kwargs_vcoord['levels'] = [field_info[k]]

            if kwargs_vcoord['typeoffirstfixedsurface'] != 119:  # hybrid-pressure
                kwargs_vcoord.pop('grid', None)
            vcoordinate = fpx.geometry(**kwargs_vcoord)
            # Prepare field dimensions
            spectral = encoding['spectral']
            if spectral:
                truncation = self.spectral_geometry.truncation
                if 'fourier' in self.spectral_geometry.space:
                    # LAM
                    SPdatasize = wtransforms.w_etrans_inq(self.geometry.dimensions['X'],
                                                          self.geometry.dimensions['Y'],
                                                          self.geometry.dimensions['X_CIzone'],
                                                          self.geometry.dimensions['Y_CIzone'],
                                                          truncation['in_X'], truncation['in_Y'],
                                                          config.KNUMMAXRESOL,
                                                          self.geometry.grid['X_resolution'],
                                                          self.geometry.grid['Y_resolution'])[1]
                elif self.spectral_geometry.space == 'legendre':
                    # Global
                    total_system_memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
                    memory_needed_for_transforms = truncation['max'] ** 3 / 2 * 8
                    if memory_needed_for_transforms >= config.prevent_swapping_legendre * total_system_memory:
                        raise epygramError('Legendre spectral transforms need ' + \
                                           str(int(float(memory_needed_for_transforms) / (1024 ** 2.))) + \
                                           ' MB memory, while only ' + \
                                           str(int(float(total_system_memory) / (1024 ** 2.))) + \
                                           ' MB is available: SWAPPING prevented !')
                    SPdatasize = wtransforms.w_trans_inq(self.geometry.dimensions['lat_number'],
                                                         truncation['max'],
                                                         len(self.geometry.dimensions['lon_number_by_lat']),
                                                         numpy.array(self.geometry.dimensions['lon_number_by_lat']),
                                                         config.KNUMMAXRESOL)[1]
                    SPdatasize *= 2  # complex coefficients
                datasize = SPdatasize
                spectral_geometry = self.spectral_geometry
            else:
                if self.geometry.rectangular_grid:
                    GPdatasize = self.geometry.dimensions['X'] * self.geometry.dimensions['Y']
                else:
                    GPdatasize = sum(self.geometry.dimensions['lon_number_by_lat'])
                datasize = GPdatasize
                spectral_geometry = None
            # Make geometry object
            kwargs_geom = dict(structure='H2D',
                               name=self.geometry.name,
                               grid=copy.copy(self.geometry.grid),
                               dimensions=self.geometry.dimensions,
                               vcoordinate=vcoordinate,
                               position_on_horizontal_grid=self.geometry.position_on_horizontal_grid,
                               geoid=config.FA_default_geoid)
            if self.geometry.projected_geometry:
                kwargs_geom['projection'] = self.geometry.projection
            geometry = fpx.geometry(**kwargs_geom)

        # Get data if requested
        if getdata:
            if field_info['type'] == 'Misc':
                field_length = wlfi.wlfinfo(self._unit, fieldname)[0]
                data = wfa.wfalais(self._unit, fieldname, field_length)
                if field_info['dimension'] == 0:
                    if field_info['nature'] == 'int':
                        dataOut = data.view('int64')[0]
                    elif field_info['nature'] == 'str':
                        dataInt = data.view('int64')
                        dataOut = ""
                        for num in dataInt:
                            dataOut += chr(num)
                    elif field_info['nature'] == 'bool':
                        dataOut = bool(data.view('int64')[0])
                    elif field_info['nature'] == 'float':
                        dataOut = data[0]
                    else:
                        raise NotImplementedError("reading of datatype " + \
                                                  field_info['nature'] + ".")
                else:
                    # copy is necessary for garbage collector
                    if field_info['nature'] == 'int':
                        dataOut = numpy.copy(data.view('int64')[:])
                    elif field_info['nature'] == 'float':
                        dataOut = numpy.copy(data)
                    elif field_info['nature'] == 'str':
                        raise NotImplementedError("reading of datatype " + \
                                                  field_info['nature'] + " array.")
                        dataOut = numpy.copy(data)
                    elif field_info['nature'] == 'bool':
                        dataOut = numpy.copy(data.view('bool')[:])
                    else:
                        raise NotImplementedError("reading of datatype " + \
                                                  field_info['nature'] + " array.")
                data = dataOut
            elif field_info['type'] == 'H2D':
                if config.spectral_coeff_order == 'model':
                    data = numpy.array(wfa.wfacilo(datasize,
                                                   self._unit,
                                                   fieldname[0:4],
                                                   0,
                                                   fieldname[4:],
                                                   spectral))
                else:
                    #CLEANME: when everybody can use facilo (CY41T1_op1 onwards)
                    data = numpy.array(wfa.wfacile(datasize,
                                                   self._unit,
                                                   fieldname[0:4],
                                                   0,
                                                   fieldname[4:],
                                                   spectral))

        # Create field
        fid = {self.format:fieldname}
        if field_info['type'] == 'H2D':
            # Create H2D field
            fid['generic'] = FPDict(get_generic_fid(fieldname))
            cumul = field_info.get('productDefinitionTemplateNumber', None)
            if cumul is None or cumul == 0:
                validity = FieldValidity(basis=self.validity.getbasis(),
                                         term=self.validity.term())
            else:
                validity = self.validity.deepcopy()
                validity.set(statistical_process_on_duration=inquire_field_dict(fieldname).get('typeOfStatisticalProcessing', None))
            # MOCAGE surface fields: different terms can be stored in one file !
            if self.validity.multi and fieldname[0:2] in ('SF', 'EM', 'DV') and \
               all([c.isdigit() for c in fieldname[2:4]]):
                term_in_seconds = datetime.timedelta(seconds=3600 * int(fieldname[2:4]))
                validity.set(term=term_in_seconds)
            field = builder(fid=fid,
                            structure=geometry.structure,
                            geometry=geometry,
                            validity=validity,
                            spectral_geometry=spectral_geometry,
                            processtype=self.processtype)
            if 'gauss' in self.geometry.name and config.FA_buffered_gauss_grid:
                # trick: link the gauss lonlat grid so that it can be shared by
                # several geometry objects or fields !
                if not hasattr(self.geometry, '_buffered_gauss_grid'):
                    (igrid, jgrid) = self.geometry._allocate_colocation_grid(compressed=False, as_float=True)
                    self.geometry._buffered_gauss_grid = {'lons':igrid,
                                                          'lats':jgrid,
                                                          'filled':False}
                field.geometry._buffered_gauss_grid = self.geometry._buffered_gauss_grid
        elif field_info['type'] == 'Misc':
            # Create Misc field
            fid['generic'] = FPDict()
            field = builder(fid=fid)
        if getdata:
            if field_info['type'] == 'H2D' and not field.spectral:
                data = geometry.reshape_data(data, 1)
            field.setdata(data)

        return field

    def readfields(self, requestedfields=None, getdata=True):
        """
        Returns a :class:`epygram.base.FieldSet` containing requested fields
        read in the resource.

        Args: \n
        - *requestedfields*: might be \n
          - a regular expression (e.g. 'S\*WIND.[U,V].PHYS')
          - a list of FA fields identifiers with regular expressions (e.g.
            ['SURFTEMPERATURE', 'S0[10-20]WIND.?.PHYS'])
          - if not specified, interpretated as all fields that will be found in
            resource
        - *getdata*: optional, if *False*, only metadata are read, the fields
          do not contain data. Default is *True*.
        """

        requestedfields = self.find_fields_in_resource(requestedfields)
        if requestedfields == []:
            raise epygramError("unable to find requested fields in resource.")

        return super(FA, self).readfields(requestedfields, getdata)

    def writefield(self, field, compression=None):
        """
        Write a field in the resource.
        
        Args: \n
        - *field*: a :class:`epygram.base.Field` instance or
          :class:`epygram.fields.H2DField`.
        - *compression*: optional, a (possibly partial) dict containing
          parameters for field compression (in case of a
          :class:`epygram.fields.H2DField`). Ex: {'KNGRIB': 2, 'KDMOPL': 5,
          'KPUILA': 1, 'KSTRON': 10, 'KNBPDG': 24, 'KNBCSP': 24}
        """

        if self.openmode == 'r':
            raise IOError("cannot write field in a FA with openmode 'r'.")

        if not self.isopen:
            if not isinstance(field, H2DField):
                # FA need a geometry to be open. Maybe this FA has not been
                # given one at opening. For opening it, either write a H2DField
                # in, or call its method open(geometry, validity), geometry
                # being a D3Geometry (or heirs), validity being a FieldValidity.
                raise epygramError("cannot write a this kind of field on a" + \
                                   " non-open FA.")
            if self.validity == None:
                if len(field.validity) != 1:
                    raise epygramError("FA can hold only one validity.")
                self._attributes['validity'] = field.validity
            self._attributes['headername'] = _create_header_from_geometry(field.geometry,
                                                                          field.spectral_geometry)
            self.open()

        if not self.empty and field.fid[self.format] in self.listfields():
            epylog.info("there already is a field with the same name in" + \
                        " this FA: overwrite.")

        if isinstance(field, MiscField):
            data = field.data
            if field.shape in ((1,), ()):
                if 'int' in field.datatype.name:
                    dataReal = data.view('float64')
                elif 'str' in field.datatype.name:
                    data = str(data)  # ndarray of str -> simple str
                    dataReal = numpy.array([ord(d) for d in data]).view('float64')
                elif 'bool' in field.datatype.name:
                    dataReal = numpy.array(1 if data else 0).view('float64')
                elif 'float' in  field.datatype.name:
                    dataReal = data
                else:
                    raise NotImplementedError("writing of datatype " + \
                                              field.datatype.name + ".")
            else:
                try:
                    dataReal = numpy.copy(data.view('float64'))
                except Exception:
                    raise NotImplementedError("writing of datatype " + \
                                              field.datatype.__name__ + \
                                              " array.")
            wfa.wfaisan(self._unit,
                        field.fid[self.format],
                        dataReal.size,
                        dataReal)

        elif isinstance(field, H2DField):
            assert [self.geometry.name, self.geometry.dimensions] == \
                   [field.geometry.name, field.geometry.dimensions], \
                   "gridpoint geometry incompatibility: a FA can hold only one geometry."
            if field.geometry.vcoordinate.grid and field.geometry.vcoordinate.typeoffirstfixedsurface == 119:
                # tolerant check because of encoding differences in self
                l = len(self.geometry.vcoordinate.grid['gridlevels'])
                s = self.geometry.vcoordinate.grid['gridlevels']
                f = field.geometry.vcoordinate.grid['gridlevels']
                diffmax = max(numpy.array([s[k][1]['Ai'] - f[k][1]['Ai'] for k in range(l)]).max(),
                              numpy.array([s[k][1]['Bi'] - f[k][1]['Bi'] for k in range(l)]).max())
            else:
                diffmax = 0.
            assert diffmax < 1e-10 or not field.geometry.vcoordinate.grid, \
                   "vertical geometry mismatch between field and file."

            if field.spectral_geometry != None and\
               field.spectral_geometry != self.spectral_geometry:
                # compatibility check
                raise epygramError("spectral geometry incompatibility:" + \
                                   " a FA can hold only one geometry.")
            if self.validity.cumulativeduration() is None and field.validity.cumulativeduration() is not None:
                self.validity.set(cumulativeduration=field.validity.cumulativeduration())
                self._set_validity()
            data = numpy.ma.copy(field.data).flatten()
            if isinstance(data, numpy.ma.core.MaskedArray):
                data = numpy.copy(data[data.mask == False].data)
            if compression is not None:
                modified_compression = True
            elif self.fieldscompression.has_key(field.fid[self.format]):
                compression = self.fieldscompression[field.fid[self.format]]
                modified_compression = True
            else:
                modified_compression = False
                compression = self._getrunningcompression()
            if modified_compression:
                self._setrunningcompression(**compression)
            #FIXME: next export version
            if config.spectral_coeff_order == 'model':
                wfa.wfaieno(self._unit,
                            field.fid[self.format][0:4],
                            0,
                            field.fid[self.format][4:],
                            len(data), data,
                            field.spectral)
            else:
                #CLEANME: when everybody can use facilo (CY41T1_op1 onwards)
                wfa.wfaienc(self._unit,
                            field.fid[self.format][0:4],
                            0,
                            field.fid[self.format][4:],
                            len(data), data,
                            field.spectral)
            if modified_compression:
                # set back to default
                self._setrunningcompression(**self.default_compression)
            if not self.fieldscompression.has_key(field.fid[self.format]):
                self.fieldscompression[field.fid[self.format]] = compression
        if self.empty: self.empty = False

    def writefields(self, fieldset, compression=None):
        """
        Write the fields of the *fieldset* in the resource.

        Args: \n
        - *fieldset*: must be a :class:`epygram.base.FieldSet` instance.
        - *compression*: must be a list of compression dicts
          (cf. *writefield()* method), of length equal to the length of the
          *fieldset*, and with the same order.
        """

        if not isinstance(fieldset, FieldSet):
            raise epygramError("'fieldset' argument must be of kind FieldSet.")
        if len(fieldset) != len(compression):
            raise epygramError("fieldset and compression must have the same" + \
                               " length.")

        # Be sure the first field to be written is an H2DField,
        # for being able to set the header if necessary
        if not self.isopen and not isinstance(fieldset[0], H2DField):
            for f in range(len(fieldset)):
                if isinstance(fieldset[f], H2DField):
                    fieldset.insert(0, fieldset.pop(f))
                    if compression != None:
                        compression.insert(0, compression.pop(f))
                    break

        if compression != None:
            # loop separated from the above one,
            # because fieldset is there-above modified
            for f in range(len(fieldset)):
                self.writefield(fieldset[f], compression[f])
        else:
            super(FA, self).writefields(fieldset)

    def rename_field(self, fieldname, new_name):
        """
        Renames a field "in place".
        """
        wlfi.wlfiren(self._unit, fieldname, new_name)

    def delfield(self, fieldname):
        """
        Deletes a field from file "in place".
        """
        wlfi.wlfisup(self._unit, fieldname)

    @FileResource._openbeforedelayed
    def extractprofile(self, pseudoname, lon=None, lat=None,
                       geometry=None,
                       vertical_coordinate=None,
                       interpolation='nearest',
                       cheap_height=True,
                       external_distance=None):
        """
        Extracts a vertical profile from the FA resource, given its pseudoname
        and the geographic location (*lon*/*lat*) of the profile.
        
        Args: \n
        - *pseudoname* must have syntax: 'K\*PARAMETER',
          K being the kind of surface (S,P,H,V),
          \* being a true star character,
          and PARAMETER being the name of the parameter requested,
          as named in FA.
        - *lon* is the longitude of the desired point.
        - *lat* is the latitude of the desired point.
        - *geometry* is the geometry on which extract data. If None, it is built from
          lon/lat.
        - *vertical_coordinate* defines the requested vertical coordinate of the 
          V1DField (as number of GRIB2 norm: http://apps.ecmwf.int/codes/grib/format/grib2/ctables/4/5).
        - *interpolation* defines the interpolation function used to compute 
          the profile at requested lon/lat from the fields grid:
          - if 'nearest' (default), extracts profile at the horizontal nearest neighboring gridpoint;
          - if 'linear', computes profile with horizontal linear spline interpolation;
          - if 'cubic', computes profile with horizontal cubic spline interpolation.
        - *cheap_height*: if True and *vertical_coordinate* among
          ('altitude', 'height'), the computation of heights is done without
          taking hydrometeors into account (in R computation) nor NH Pressure
          departure (Non-Hydrostatic data). Computation therefore faster.
        - *external_distance* can be a dict containing the target point value
          and an external field on the same grid as self, to which the distance
          is computed within the 4 horizontally nearest points; e.g. 
          {'target_value':4810, 'external_field':an_H2DField_with_same_geometry}.
          If so, the nearest point is selected with
          distance = |target_value - external_field.data|
        """

        if geometry is None:
            if None in [lon, lat]:
                raise epygramError("You must give a geometry or lon *and* lat")
            if self.geometry == None: self._read_geometry()
            pointG = self.geometry.make_point_geometry(lon, lat)
        else:
            if lon != None or lat != None:
                raise epygramError("You cannot provide lon or lat when geometry is given")
            if geometry.structure != "V1D":
                raise epygramError("geometry must be a V1D")
            pointG = geometry

        profile = self.extract_subdomain(pseudoname, pointG,
                                         interpolation=interpolation,
                                         vertical_coordinate=vertical_coordinate,
                                         external_distance=external_distance,
                                         cheap_height=cheap_height)

        return profile

    @FileResource._openbeforedelayed
    def extractsection(self, pseudoname, end1=None, end2=None,
                       geometry=None, points_number=None,
                       resolution=None, vertical_coordinate=None,
                       interpolation='linear',
                       cheap_height=True):
        """
        Extracts a vertical section from the FA resource, given its pseudoname
        and the geographic (lon/lat) coordinates of its ends.
        The section is returned as a V2DField.
        
        Args: \n
        - *pseudoname* must have syntax: 'K\*PARAMETER',
          K being the kind of surface (S,P,H,V),
          \* being a true star character,
          and PARAMETER being the name of the parameter requested, as named in
          FA.
        - *end1* must be a tuple (lon, lat).
        - *end2* must be a tuple (lon, lat).
        - *geometry* is the geometry on which extract data. If None, defaults to
          linearily spaced positions computed from  *points_number*.
        - *points_number* defines the total number of horizontal points of the 
          section (including ends). If None, defaults to a number computed from
          the *ends* and the *resolution*.
        - *resolution* defines the horizontal resolution to be given to the 
          field. If None, defaults to the horizontal resolution of the field.
        - *vertical_coordinate* defines the requested vertical coordinate of the 
          V2DField (cf. :class:`epygram.geometries.V1DGeometry` coordinate
          possible values).
        - *interpolation* defines the interpolation function used to compute 
          the profile points locations from the fields grid: \n
          - if 'nearest', each horizontal point of the section is 
            taken as the horizontal nearest neighboring gridpoint;
          - if 'linear' (default), each horizontal point of the section is 
            computed with linear spline interpolation;
          - if 'cubic', each horizontal point of the section is 
            computed with linear spline interpolation.
        - *cheap_height*: if True and *vertical_coordinate* among
          ('altitude', 'height'), the computation of heights is done without
          taking hydrometeors into account (in R computation) nor NH Pressure
          departure (Non-Hydrostatic data). Computation therefore faster.
        """

        if geometry is None:
            if None in [end1, end2]:
                raise epygramError("You must give a geometry or end1 *and* end2")
            if self.geometry == None: self._read_geometry()
            sectionG = self.geometry.make_section_geometry(end1, end2,
                                                           points_number=points_number,
                                                           resolution=resolution)
        else:
            if end1 != None or end2 != None:
                raise epygramError("You cannot provide end1 or end2 when geometry is given")
            if geometry.structure != "V2D":
                raise epygramError("geometry must be a V2D")
            sectionG = geometry

        section = self.extract_subdomain(pseudoname, sectionG,
                                         interpolation=interpolation,
                                         vertical_coordinate=vertical_coordinate,
                                         cheap_height=cheap_height)

        return section

    @FileResource._openbeforedelayed
    def extract_subdomain(self, pseudoname, geometry, vertical_coordinate=None,
                          interpolation='linear', cheap_height=True,
                          external_distance=None):
        """
        Extracts a subdomain from the FA resource, given its fid
        and the geometry to use.
        
        Args: \n
        - *pseudoname* must have syntax: 'K\*PARAMETER',
          K being the kind of surface (S,P,H,V),
          \* being a true star character,
          and PARAMETER being the name of the parameter requested, as named in
          FA.
        - *geometry* is the geometry on which extract data.
        - *vertical_coordinate* defines the requested vertical coordinate of the 
          V2DField (cf. :class:`epygram.geometries.V1DGeometry` coordinate
          possible values).
        - *interpolation* defines the interpolation function used to compute 
          the profile points locations from the fields grid: \n
          - if 'nearest', each horizontal point of the section is 
            taken as the horizontal nearest neighboring gridpoint;
          - if 'linear' (default), each horizontal point of the section is 
            computed with linear spline interpolation;
          - if 'cubic', each horizontal point of the section is 
            computed with linear spline interpolation.
        - *cheap_height*: if True and *vertical_coordinate* among
          ('altitude', 'height'), the computation of heights is done without
          taking hydrometeors into account (in R computation) nor NH Pressure
          departure (Non-Hydrostatic data). Computation therefore faster.
        """
        fidlist = self.find_fields_in_resource(seed=pseudoname,
                                               fieldtype=['H2D', '3D'],
                                               generic=True)
        if fidlist == []:
            raise epygramError("cannot find profile for " + str(pseudoname) + \
                               " in resource.")
        # find the prevailing type of level
        leveltypes = [f[1]['typeOfFirstFixedSurface'] for f in fidlist]
        if len(set(leveltypes)) > 1:
            leveltypes_num = {t:0 for t in set(leveltypes)}
            for t in leveltypes:
                leveltypes_num[t] += 1
            leveltypes = [k for k, v in leveltypes_num.items() if
                          v == max(leveltypes_num.values())]
            if len(leveltypes) > 1:
                raise epygramError("unable to determine type of level" + \
                                   " to select.")
        leveltype = leveltypes[0]
        # filter by type of level
        fidlist = [f[0] for f in fidlist if f[1]['typeOfFirstFixedSurface'] == leveltype]

        field3d = fpx.field(fid={'FA':pseudoname},
                            structure='3D',
                            resource=self, resource_fids=fidlist)
        if field3d.spectral:
            field3d.sp2gp()
        subdomain = field3d.extract_subdomain(geometry,
                                              interpolation=interpolation,
                                              exclude_extralevels=True)

        # preparation for vertical coords conversion
        if vertical_coordinate not in (None, subdomain.geometry.vcoordinate.typeoffirstfixedsurface):
            # choose vertical_mean with regards to H/NH
            if 'S001PRESS.DEPART' in self.listfields():
                vertical_mean = 'geometric'
            else:
                vertical_mean = 'arithmetic'
            # surface pressure (hybridP => P,A,H)
            if subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 119 and \
               vertical_coordinate in (100, 102, 103):
                Psurf = self.readfield('SURFPRESSION')
                if Psurf.spectral:
                    Psurf.sp2gp()
                ps_transect = numpy.exp(Psurf.getvalue_ll(*geometry.get_lonlat_grid(),
                                                          interpolation=interpolation,
                                                          one=False,
                                                          external_distance=external_distance))
                del Psurf
            # P => H necessary profiles
            if vertical_coordinate in (102, 103):
                side_profiles = {'t':'*TEMPERATURE',
                                 'q':'*HUMI.SPECIFI',
                                 'pdep':'*PRESS.DEPART',
                                 'ql':'*CLOUD_WATER',
                                 'qi':'*ICE_CRYSTAL',
                                 'qs':'*SNOW',
                                 'qr':'*RAIN',
                                 'qg':'*GRAUPEL'}
                for p in sorted(side_profiles.keys(), reverse=True):  # reverse to begin by t
                    try:
                        # try to extract profiles for each lon/lat and each parameter
                        if pseudoname == side_profiles[p]:
                            # already extracted as requested profile
                            side_profiles[p] = subdomain
                        else:
                            if cheap_height and p not in ('t', 'q'):
                                raise epygramError()  # to go through "except" instructions below
                            side_profiles[p] = self.extract_subdomain(side_profiles[p], geometry,
                                                                      interpolation=interpolation,
                                                                      external_distance=external_distance)
                        side_profiles[p] = side_profiles[p].getdata()
                    except epygramError:
                        # fields not present in file
                        if p in ('t', 'q'):
                            raise epygramError("Temperature and Specific" + \
                                               " Humidity must be in" + \
                                               " resource.")
                        else:
                            side_profiles[p] = numpy.zeros(side_profiles['t'].shape)
                R = util.gfl2R(*[side_profiles[p] for p in
                                 ['q', 'ql', 'qi', 'qr', 'qs', 'qg']])
            if vertical_coordinate == 102:
                try:
                    geopotential = self.readfield('SPECSURFGEOPOTEN')
                except epygramError:
                    geopotential = self.readfield('SURFGEOPOTENTIEL')
                else:
                    geopotential.sp2gp()
                surface_geopotential = geopotential.getvalue_ll(*geometry.get_lonlat_grid(),
                                                                interpolation=interpolation,
                                                                one=False,
                                                                external_distance=external_distance)
                del geopotential
            else:
                surface_geopotential = numpy.zeros(geometry.get_lonlat_grid()[0].size)

            # effective vertical coords conversion
            if subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 119 and \
               vertical_coordinate == 100:
                subdomain.geometry.vcoordinate = hybridP2pressure(subdomain.geometry.vcoordinate,
                                                                  ps_transect,
                                                                  vertical_mean,
                                                                  gridposition='mass')
            elif subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 119 and \
                 vertical_coordinate in (102, 103):
                subdomain.geometry.vcoordinate = hybridP2altitude(subdomain.geometry.vcoordinate,
                                                                  R,
                                                                  side_profiles['t'],
                                                                  ps_transect,
                                                                  vertical_mean,
                                                                  Pdep=side_profiles['pdep'],
                                                                  Phi_surf=surface_geopotential)
            elif subdomain.geometry.vcoordinate.typeoffirstfixedsurface == 100 and \
                 vertical_coordinate in (102, 103):
                subdomain.geometry.vcoordinate = pressure2altitude(subdomain.geometry.vcoordinate,
                                                                   R,
                                                                   side_profiles['t'],
                                                                   vertical_mean,
                                                                   Pdep=side_profiles['pdep'],
                                                                   Phi_surf=surface_geopotential)
            else:
                raise NotImplementedError("this vertical coordinate" + \
                                          " conversion.")

        return subdomain

###########
# pre-app #
###########

    @FileResource._openbeforedelayed
    def what(self, out=sys.stdout,
             details=None,
             sortfields=False,
             **kwargs):
        """
        Writes in file a summary of the contents of the FA.

        Args: \n
        - *out*: the output open file-like object (duck-typing: *out*.write()
          only is needed).
        - *details*: 'spectral' if spectralness of fields is requested;
                     'compression' if information about fields compression
                     is requested.
        - *sortfields*: **True** if the fields have to be sorted by type.
        - *fastlist
        """

        for f in self.listfields():
            if inquire_field_dict(f)['type'] == 'H2D':
                first_H2DField = f
                break
        if len(self.listfields()) == 0:
            raise epygramError('empty Resource.')
        firstfield = self.readfield(first_H2DField, getdata=False)
        if not firstfield.spectral and self.spectral_geometry != None:
            firstfield._attributes['spectral_geometry'] = self.spectral_geometry

        listoffields = self.listfields()
        if sortfields:
            sortedfields = self.sortfields()

        out.write("### FORMAT: " + self.format + "\n")
        out.write("\n")
        out.write("### IDENTIFIER (CDIDEN): " + self.cdiden + "\n")
        out.write("\n")

        FieldValidityList(self.validity).what(out)
        firstfield.what(out,
                        validity=False,
                        vertical_geometry=False,
                        arpifs_var_names=True,
                        fid=False)

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
        if details is None:
            write_formatted_fields(out, "Field name")
        elif details == 'spectral':
            write_formatted_fields(out, "Field name", "Spectral")
        elif details == 'compression':
            params = ['KNGRIB', 'KNBITS', 'KSTRON', 'KPUILA']
            width_cp = 8
            compressionline = ""
            for p in params:
                compressionline += '{:^{width}}'.format(p, width=width_cp)
            write_formatted_fields(out, "Field name", "Spectral",
                                   compressionline)
        out.write(separation_line)
        for f in listoffields:
            if details is not None and inquire_field_dict(f)['type'] == 'H2D':
                encoding = self.fieldencoding(f)
                if details == 'spectral':
                    write_formatted_fields(out, f, encoding['spectral'])
                elif details == 'compression':
                    compressionline = ""
                    for p in params:
                        try:
                            compressionline += '{:^{width}}'.format(str(encoding[p]), width=width_cp)
                        except KeyError:
                            compressionline += '{:^{width}}'.format('-', width=width_cp)
                    write_formatted_fields(out, f, encoding['spectral'],
                                           compression=compressionline)
            else:
                write_formatted_fields(out, f)
        out.write(separation_line)



##############
# the FA WAY #
##############

    def _read_geometry(self):
        """
        Reads the geometry in the FA header.
        Interface to Fortran routines from 'ifsaux'.
        """

        (KTYPTR, PSLAPO, PCLOPO, PSLOPO,
         PCODIL, KTRONC,
         KNLATI, KNXLON, KNLOPA, KNOZPA, PSINLA,
         KNIVER, PREFER, PAHYBR, PBHYBR) = wfa.wfacies(self._FAsoftware_cst['JPXPAH'],
                                                       self._FAsoftware_cst['JPXIND'],
                                                       self._FAsoftware_cst['JPXGEO'],
                                                       self._FAsoftware_cst['JPXNIV'],
                                                       self.headername)[:-1]
        Ai = [c * PREFER for c in PAHYBR[0:KNIVER + 1]]
        Bi = [c for c in PBHYBR[0:KNIVER + 1]]
        vertical_grid = {'gridlevels': tuple([(i + 1, FPDict({'Ai':Ai[i], 'Bi':Bi[i]})) for
                                         i in range(len(Ai))]),
                         'ABgrid_position':'flux'}
        kwargs_vcoord = {'structure': 'V',
                         'typeoffirstfixedsurface':119,
                         'position_on_grid': 'mass',
                         'grid': vertical_grid,
                         'levels': list([i + 1 for i in range(len(Ai) - 1)])
                        }
        vcoordinate_read_in_header = fpx.geometry(**kwargs_vcoord)
        self.reference_pressure = PREFER

        rectangular_grid = KTYPTR <= 0
        if rectangular_grid:
            LMAP = int(PCODIL) != -1
            # LAM or regular lat/lon
            projected_geometry = (int(PSINLA[0]) != 0 and int(PSINLA[1]) != -9) or \
                                 (int(PSINLA[0]) == 0 and int(PSINLA[9]) != -9)  # "new" or "old" header
            dimensions = {'X':KNXLON,
                          'Y':KNLATI}
            if projected_geometry:
                # LAM (projection)
                dimensions.update({'X_CIzone':KNLOPA[3] - KNLOPA[2] + 1,
                                   'Y_CIzone':KNLOPA[5] - KNLOPA[4] + 1,
                                   'X_Iwidth':KNLOPA[6],
                                   'Y_Iwidth':KNLOPA[7],
                                   'X_Czone':KNLOPA[3] - KNLOPA[2] + 1 - 2 * KNLOPA[6],
                                   'Y_Czone':KNLOPA[5] - KNLOPA[4] + 1 - 2 * KNLOPA[7],
                                   })
                if int(PSINLA[0]) != 0:
                    grid = {'X_resolution':PSINLA[6],
                            'Y_resolution':PSINLA[7]}
                else:
                    grid = {'X_resolution':PSINLA[14],
                            'Y_resolution':PSINLA[15]}
                if KNLOPA[1] == 0:  # C+I
                    grid['LAMzone'] = 'CI'
                    dimensions['X'] = dimensions['X_CIzone']
                    dimensions['Y'] = dimensions['Y_CIzone']
                    io, jo = 0, 0
                elif abs(KNLOPA[1]) == 1:  # C+I+E
                    grid['LAMzone'] = 'CIE'
                    io, jo = KNLOPA[2] - 1, KNLOPA[4] - 1
                    dimensions['X_CIoffset'] = io
                    dimensions['Y_CIoffset'] = jo
                if LMAP and int(PSINLA[0]) == 0:
                    # 'old' header : from A. Stanesic (Croatia)
                    projection = {'reference_lon':Angle(PSINLA[7], 'radians'),
                                  'reference_lat':Angle(PSINLA[8], 'radians'),
                                  'rotation':Angle(0., 'radians')}
                    grid.update({'input_lon':Angle(PSINLA[7], 'radians'),
                                 'input_lat':Angle(PSINLA[8], 'radians'),
                                 'input_position':(io + (float(dimensions['X_CIzone']) - 1) / 2.,
                                                   jo + (float(dimensions['Y_CIzone']) - 1) / 2.)})
                    PSINLA[1] = PSINLA[9]
                    PSINLA[0] = 0
                elif LMAP and int(PSINLA[0]) != 0:
                    projection = {'reference_lon':Angle(PSINLA[2], 'radians'),
                                  'reference_lat':Angle(PSINLA[3], 'radians'),
                                  'rotation':Angle(0., 'radians')}
                    grid.update({'input_lon':Angle(PSINLA[4], 'radians'),
                                 'input_lat':Angle(PSINLA[5], 'radians'),
                                 'input_position':(io + (float(dimensions['X_CIzone']) - 1) / 2.,
                                                   jo + (float(dimensions['Y_CIzone']) - 1) / 2.)})

                if abs(PSINLA[1]) <= config.epsilon:
                    geometryname = 'mercator'
                elif 1.0 - abs(PSINLA[1]) <= config.epsilon:
                    geometryname = 'polar_stereographic'
                elif config.epsilon < abs(PSINLA[1]) < 1.0 - config.epsilon:
                    geometryname = 'lambert'
                spectral_space = 'bi-fourier'
                spectral_trunc = {'in_X':KTYPTR * -1,
                                  'in_Y':KTRONC,
                                  'shape':'elliptic'}
                if not LMAP:
                    if dimensions['X'] == 1:
                        spectral_space = 'fourier'
                        spectral_trunc = {'in_Y':KTRONC,
                                          'in_X':KTYPTR * -1}
                    projection = None
                    geometryname = 'academic'
            elif not projected_geometry:
                # regular lat/lon
                projection = None
                geometryname = 'regular_lonlat'
                if int(PSINLA[0]) == 0:
                    # 'old' header
                    grid = {'input_lon':Angle(PSINLA[3], 'radians'),
                            'input_lat':Angle(PSINLA[4], 'radians'),
                            'input_position':(0, 0),
                            'X_resolution':Angle(PSINLA[14], 'radians'),
                            'Y_resolution':Angle(PSINLA[15], 'radians')}
                else:
                    grid = {'input_lon':Angle(PSINLA[4], 'radians'),
                            'input_lat':Angle(PSINLA[5], 'radians'),
                            'input_position':((float(dimensions['X']) - 1) / 2.,
                                              (float(dimensions['Y']) - 1) / 2.),
                            'X_resolution':Angle(PSINLA[6], 'radians'),
                            'Y_resolution':Angle(PSINLA[7], 'radians')}
                spectral_space = None
        else:
            # ARPEGE global
            projection = None
            # reconstruction of tables on both hemispheres
            KNLOPA = KNLOPA[:KNLATI / 2]
            KNOZPA = KNOZPA[:KNLATI / 2]
            PSINLA = PSINLA[:KNLATI / 2]
            lon_number_by_lat = [n for n in KNLOPA] + [KNLOPA[-(n + 1)] for n in
                                                       range(0, len(KNLOPA))]
            max_zonal_wavenumber_by_lat = [n for n in KNOZPA] + \
                                          [KNOZPA[-(n + 1)] for n in
                                           range(0, len(KNOZPA))]
            latitudes = [Angle((math.cos(math.asin(sinlat)), sinlat), 'cos_sin') for sinlat in PSINLA] \
                        + [Angle((math.cos(math.asin(PSINLA[-(n + 1)])), -PSINLA[-(n + 1)]), 'cos_sin') for n in range(0, len(PSINLA))]
            grid = {'dilatation_coef':PCODIL,
                    'latitudes':FPList([l for l in latitudes])
                    }
            if KTYPTR == 1:
                geometryname = 'reduced_gauss'
            elif KTYPTR == 2:
                geometryname = 'rotated_reduced_gauss'
                grid['pole_lat'] = Angle((math.cos(math.asin(PSLAPO)), PSLAPO),
                                         'cos_sin')
                grid['pole_lon'] = Angle((PCLOPO, PSLOPO), 'cos_sin')
            dimensions = {'max_lon_number':KNXLON,
                          'lat_number':KNLATI,
                          'lon_number_by_lat':FPList([n for n in
                                                      lon_number_by_lat])
                              }
            spectral_space = 'legendre'
            spectral_trunc = {'max':KTRONC,
                              'shape':'triangular',
                              'max_zonal_wavenumber_by_lat':FPList([k for k in
                                                                    max_zonal_wavenumber_by_lat])
                              }
        kwargs_geom = dict(structure='3D',
                           name=geometryname,
                           grid=grid,
                           dimensions=dimensions,
                           vcoordinate=vcoordinate_read_in_header,
                           position_on_horizontal_grid='center',
                           geoid=config.FA_default_geoid)
        if projection is not None:
            kwargs_geom['projection'] = projection
        self.geometry = fpx.geometry(**kwargs_geom)
        if spectral_space is not None:
            self.spectral_geometry = SpectralGeometry(space=spectral_space,
                                                      truncation=spectral_trunc)
        else:
            self.spectral_geometry = None

    @FileResource._openbeforedelayed
    def _read_validity(self):
        """
        Reads the validity in the FA header.
        Interface to Fortran routines from 'ifsaux'.
        """

        KDATEF = wfa.wfadiex(self._unit)
        year = int(KDATEF[0])
        month = int(KDATEF[1])
        day = int(KDATEF[2])
        hour = int(KDATEF[3])
        minute = int(KDATEF[4])
        second = int(KDATEF[13]) - hour * 3600 - minute * 60
        if second >= 60:
            m = second // 60
            second = second % 60
            if m > 60:
                hour += m // 60
            minute = m % 60
        processtype = int(KDATEF[8])
        if   processtype == 0:
            self.processtype = 'analysis'
        elif processtype == 1:
            self.processtype = 'initialization'
        elif processtype == 9:
            self.processtype = 'forcings'
        elif processtype == 10:
            self.processtype = 'forecast'
        term_in_seconds = int(KDATEF[14])
        cumulationstart_in_seconds = int(KDATEF[15])
        cumulativeduration_in_seconds = term_in_seconds - cumulationstart_in_seconds
        basis = datetime.datetime(year, month, day, hour, minute, second)
        term = datetime.timedelta(seconds=term_in_seconds)
        cumulativeduration = datetime.timedelta(seconds=cumulativeduration_in_seconds)

        self.validity = FieldValidity(basis=basis, term=term, cumulativeduration=cumulativeduration)
        if int(KDATEF[7]) == 1:
            self.validity.multi = True
        else:
            self.validity.multi = False

    @FileResource._openbeforedelayed
    def _set_validity(self, termunit='hours'):
        """
        Sets date, hour and the processtype in the resource.
        """

        if not self.isopen:
            raise epygramError("_set_validity must be called after FA is open.")

        basis = self.validity.getbasis()
        KDATEF = numpy.zeros(22, dtype=numpy.int64)
        KDATEF[0] = int(basis.year)
        KDATEF[1] = int(basis.month)
        KDATEF[2] = int(basis.day)
        KDATEF[3] = int(basis.hour)
        KDATEF[4] = int(basis.minute)
        if termunit == 'hours':
            KDATEF[5] = 1
            KDATEF[6] = self.validity.term(fmt='IntHours')
            if self.validity.cumulativeduration() is not None:
                KDATEF[9] = self.validity.term(fmt='IntHours') - \
                            self.validity.cumulativeduration(fmt='IntHours')
        elif termunit == 'days':
            KDATEF[5] = 2
            KDATEF[6] = self.validity.term('IntHours') / 24
            if self.validity.cumulativeduration() is not None:
                KDATEF[9] = (self.validity.term('IntHours') - \
                             self.validity.cumulativeduration('IntHours')) / 24
        else:
            raise NotImplementedError("term unit other than hours/days ?")
        # KDATEF[7] = 0
        if self.processtype == 'analysis':
            processtype = 0
        elif self.processtype == 'initialization':
            processtype = 1
        elif self.processtype == 'forcings':
            processtype = 9
        elif self.processtype == 'forecast':
            processtype = 10
        else:
            processtype = 255  # unknown processtype
        KDATEF[8] = processtype
        # KDATEF[10] = 0
        if not config.FA_fandax:
            wfa.wfandar(self._unit, KDATEF)
        else:
            # fandax
            KDATEF[11] = 1
            KDATEF[13] = int(basis.second) + \
                         int(basis.minute) * 60 + \
                         int(basis.hour) * 3600
            KDATEF[14] = self.validity.term(fmt='IntSeconds')
            if self.validity.cumulativeduration() is not None:
                KDATEF[15] = self.validity.term(fmt='IntSeconds') - \
                             self.validity.cumulativeduration(fmt='IntSeconds')
            wfa.wfandax(self._unit, KDATEF)

    @FileResource._openbeforedelayed
    def _getrunningcompression(self):
        """
        Returns the current compression parameters of the FA (at time of writing).
        Interface to ifsaux' FAVEUR.
        """

        comp = dict()
        (comp['KNGRIB'], comp['KNBPDG'], comp['KNBCSP'], comp['KSTRON'],
         comp['KPUILA'], comp['KDMOPL']) = wfa.wfaveur(self._unit)

        return comp

    @FileResource._openbeforedelayed
    def _setrunningcompression(self, **kwargs):
        """
        Sets the compression parameters of the FA.
        Interface to FAGOTE (cf. FAGOTE documentation for significance of
        arguments).
        """

        if self.openmode == 'r':
            raise IOError("method _setrunningcompression() can only be" + \
                          " called if 'openmode' in('w', 'a').")
        comp = copy.deepcopy(self.default_compression)
        for k in kwargs.keys():
            if k in self.default_compression.keys():
                comp[k] = kwargs[k]
            else:
                raise epygramError("unknown parameter: " + k + \
                                   " passed as argument.")
        wfa.wfagote(self._unit,
                    comp['KNGRIB'],
                    comp['KNBPDG'],
                    comp['KNBCSP'],
                    comp['KSTRON'],
                    comp['KPUILA'],
                    comp['KDMOPL'])

#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
The module is used throughout the package to share constant parameters.

The standard default parameters lie below. They can be overwritten by
the user in the User config file :attr:`userconfigfile`.
"""

import os
import sys
import imp
import platform
import copy

import footprints



### INSTALL ###
#: Directory of epygram package install
installdir = __file__[:-(len(os.path.basename(__file__)) + 1)]
home = os.getenv('HOME')
#: User customization directory
usercustomizationdir = home + '/.epygram'
#: User config (overwrites standard config)
userconfigfile = usercustomizationdir + '/userconfig.py'
#: epygram Colormaps
epygram_colormaps = {'aspect':installdir + '/data/aspect.cmap',
                     'gaspect':installdir + '/data/gaspect.cmap',
                     'radar':installdir + '/data/radar.cmap',
                     'rr1h':installdir + '/data/rr1h.cmap',
                     'rr6h':installdir + '/data/rr24h.cmap',
                     'rr24h':installdir + '/data/rr24h.cmap',
                     }



### PARAMETERS ###
#: gravity constant
g0 = 9.80665
#: Cp dry air
Cpd = 1004.709
#: Specific gas constant, dry air
Rd = 287.059
#: Specific gas constant, water vapor
Rv = 461.524
#: Ptop: pressure @ summit of atmosphere. For vertical coordinates conversions.
default_Ptop = 0.
#: Epsilon
epsilon = sys.float_info.epsilon
#: Maximum number of truncations handled (setup spectral transforms)
KNUMMAXRESOL = 10
#: Plots sizes (in inches)
plotsizes = (16., 12.)
#: Interactive graphical backend.
#: If False, X11 is the graphical device. Non-interactive backends
#: such as 'Agg' can be used, especially without export DISPLAY
noninteractive_backend = False
#: Default output for apptools
default_graphical_output = False
if os.getenv('DISPLAY', '') == '' or \
   'beaufix' in platform.node() or \
   'prolix' in platform.node():
    noninteractive_backend = 'Agg'
    default_graphical_output = 'png'
#: Vector graphical symbol
vector_symbol = 'barbs'
#: Default quality for figures
default_figures_dpi = 150



### FORMATS ###
#: List of implemented/activated formats
#: for the formats_factory, the list is ordered by specificity for those of the
#: same family (e.g. FA before LFI, DDHLFA before LFA...)
#: Removing one of these (in userconfig file) may allow an incomplete install
#: of epygram, disabling one format.
implemented_formats = ['netCDF', 'GRIB', 'GeoPoints', 'TIFFMF', 'FA', 'LFI', 'DDHLFA', 'LFA']

#: FA default compression parameters
FA_default_compression = {'KNGRIB': 2, 'KDMOPL': 0, 'KPUILA': 0, 'KSTRON': 0,
                          'KNBPDG': 16, 'KNBCSP': 16}
#: Default reference pressure coefficient for converting hybrid A coefficients
#: in FA
FA_default_reference_pressure = 101325.
#: geoid of FA files in pyproj syntax
FA_geoid_for_pyproj = {'a':6371229., 'b':6371229.}
#: FA field dictionaries
FA_field_dictionaries_csv = {'default':installdir + '/data/Field_Dict_FA.csv',
                             'user':usercustomizationdir + '/user_Field_Dict_FA.csv'}
#: FA (write) date & time precision: use FANDAR (minute) or FANDAX (second,
#: cy40t1 onwards)
FA_fandax = True
#: To avoid re-computing lons/lats of Gauss Grids from FA each time needed:
#: makes ARPEGE profiles and section extraction times acceptable (< 1min).
FA_buffered_gauss_grid = True

#: LFA maximum number of fields
LFA_max_num_fields = 1000
#: LFA maximum length of strings (as in most LFA useage)
LFA_maxstrlen = 200

#: GeoPoints write precision for lon/lat
GeoPoints_lonlat_precision = 4
#: GeoPoints write precision for other floats
GeoPoints_precision = 6
#: GeoPoints write width of columns
GeoPoints_col_width = 12

#: GRIB default edition (1 or 2)
GRIB_default_edition = 2
#: GRIB_default tablesVersion
GRIB_default_tablesVersion = 14  # Nov. 2014 // 15 = May 2015
#: GRIB default sample (possibility to use others)
GRIB_default_sample = {i:'GRIB' + str(i) + '_second_order' for i in (1, 2)}
#GRIB_default_sample = {i:'GRIB' + str(i) for i in (1, 2)}
#: GRIB default production parameters -- write mode
GRIB_default_production_parameters = {'centre':85,  #Météo-France
                                      'generatingProcessIdentifier':254,  # last before 255=missing
                                      'productionStatusOfProcessedData':2,  # research/test
                                      'typeOfProcessedData':2,  # Analysis and forecast products
                                      'typeOfGeneratingProcess':2,  # Forecast
                                      }
#: GRIB default ordering of data
GRIB_default_ordering = {'iScansNegatively':0,
                         'jScansPositively':0,
                         'jPointsAreConsecutive':0}
#: GRIB default packing -- write mode.
#: recommended packing types for GRIB2:
#: (by increasing packing efficiency // decreasing speed performance)
#: - grid_jpeg (15% // 100%)
#: - grid_second_order (19% // 42%)
#: - grid_simple (38% // 22%)
#: - grid_ieee (100% // 12%)
GRIB_default_packing = {1:{'packingType':'grid_second_order',
                           #'complexPacking':1,
                           'boustrophedonicOrdering':1,
                           'bitsPerValue':16,
                           #'additionalFlagPresent':1,
                           },
                        2:{'packingType':'grid_second_order',
                        #2:{'packingType':'grid_simple',
                        #2:{'packingType':'grid_ieee',
                           'bitsPerValue':12,
                           }
                        }
#: GRIB samples from epygram
GRIB_samples_path = installdir + '/data'
#: satellites local GRIB2 encoding
satellites_local_GRIB2 = {'METEOSAT7':192,
                          'METEOSAT8':193,
                          'METEOSAT9':194,
                          'GOES11':195,
                          'GOES12':196,
                          'MTSAT1':197}
# sensors local GRIB2 encoding
sensors_local_GRIB2 = {'MVIRI':192,
                       'SEVIRI':193,
                       'IMAGER':194}

#: LFI field dictionaries
LFI_field_dictionaries_csv = {'default':installdir + '/data/Field_Dict_LFI.csv',
                              'user':usercustomizationdir + '/Field_Dict_LFI.csv'}
#: geoid of LFI files in pyproj syntax
LFI_geoid_for_pyproj = {'a':6371229., 'b':6371229.}

# netCDF standard dimensions
netCDF_standard_dimensions = ['N_dimension', # numerotation (obs, profile, ...)
                              'T_dimension', # time
                              'X_dimension', # X-axis (cartesian projection or longitude)
                              'Y_dimension', # Y-axis (cartesian projection or latitude)
                              'Z_dimension'] # Z-axis (vertical)
netCDF_usualnames_for_standard_dimensions = {'N_dimension':('transect', 'obs', 'profile'),
                                             'T_dimension':('time', 'validity'),
                                             'X_dimension':('xx', 'x', 'X', 'LON', 'lon', 'Nbre_lon', 'longitude', 'max_lon_number'),
                                             'Y_dimension':('yy', 'y', 'Y', 'LAT', 'lat', 'Nbre_lat', 'latitude', 'lat_number'),
                                             'Z_dimension':('zz', 'z', 'Z', 'level')}

#: netCDF default behaviour
#: the behaviour can be updated with specific netCDF_usualnames_for_standard_dimensions,
#: e.g. 'X_dimension':'nb_lon'
#: and eventual according grid, e.g. 'X_grid':'longitudes'
netCDF_default_behaviour = {# 'A_dimension':'name_of_the_dimension_in_netCDF_file',
                            # 'A_grid':'name_of_the_grid_in_netCDF_file',
                            # writing behaviours:
                            'flatten_non_rectangular_grids':False,
                            }
#: netCDF default compression
netCDF_default_compression = 4



### OPTIONS ###
#: Use home-made projection formulas (:mod:`epygram.myproj`) vs of *pyproj*.
default_projtool = 'pyproj'
#: arpifs geoid for :mod:`epygram.myproj`
myproj_default_geoid = {'geoidshape':'sphere', 'geoidradius':6371229.}
#: A classical default geoid for *pyproj*
pyproj_default_geoid = {'ellps':'WGS84'}
#: Protect unhappy writes: ask before opening a new file on an existing path
protect_unhappy_writes = False
#: Threshold on field absolute values to mask meaningless field values
mask_outside = 1e19
#: To hide footprints warnings...
hide_footprints_warnings = True
#: To raise an error if the memory needed for Legendre transforms exceeds
#: this percentage of the available memory.
prevent_swapping_legendre = 0.75
#: Use footprints.proxy builder to generate a field.
#: Defaults to False for performance reasons, but less flexible.
use_footprints_as_builder = False
#: ordering of spectral coefficients, with regards to arpifs spaces:
#: 'model' or 'FA'.
#: => 'model': read/write by wfacilo/wfaieno,
#:    'FA': read/write by wfacile/wfaienc
spectral_coeff_order = 'model'
#: To call epygram.init_env() automatically at import
#: ! Should not be True if using Vortex !
init_at_import = False



### USER CUSTOMIZATION ###
# user modules: actually should remain empty here !
#: In userconfig, this should be a list of dict containing two keys:
#: the module name and the source absolute path;
#: e.g. {'name':'mymodule', 'abspath':'/home/.../mymodule.py'}.
#:
#: The import is done in epygram *__init__.py*.
usermodules = []
# usercolormaps should also remain empty here
#: In userconfig, this should be a dict whose keys are the colormap name and
#: values the source absolute path of the colormap definition;
#: e.g. {'aspect', '/home/mary/.epygram/aspect.cmap'}.
usercolormaps = {}



### OVERWRITE WITH USER CONFIG ###
if os.path.exists(userconfigfile):
    userconfig = imp.load_source('userconfig', userconfigfile)
    from userconfig import *
    del userconfig
#: colormaps gathers epygram and user colormaps
colormaps = copy.copy(epygram_colormaps)
colormaps.update(usercolormaps)



### FURTHER INITIALIZATIONS ###
if hide_footprints_warnings:
    footprints.logger.setLevel(footprints.loggers.logging.ERROR)
# epygram workdir
#if not os.path.exists(tempdir):
#    os.mkdir(tempdir)

# Projtool defaults
if default_projtool == 'pyproj':
    #: Default geoid (case *pyproj*)
    default_geoid = pyproj_default_geoid
    #: FA default geoid (case *pyproj*)
    FA_default_geoid = FA_geoid_for_pyproj
    #: LFI default geoid (case *pyproj*)
    LFI_default_geoid = LFI_geoid_for_pyproj
elif default_projtool == 'myproj':
    #: Default geoid (case *myproj*)
    default_geoid = myproj_default_geoid
    #: FA default geoid (case *myproj*)
    FA_default_geoid = myproj_default_geoid
    #: LFI default geoid (case *myproj*)
    LFI_default_geoid = myproj_default_geoid

# Update $GRIB_SAMPLES_PATH
_gsp = os.getenv('GRIB_SAMPLES_PATH', '.') + ':' + GRIB_samples_path
os.putenv('GRIB_SAMPLES_PATH', _gsp)
del _gsp

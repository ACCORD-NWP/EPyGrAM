#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
Contains equivalences tables for GRIB encoding.
"""

from __future__ import print_function, absolute_import, unicode_literals, division

#: Aliases to *productionStatusOfProcessedData* numbers
productionStatusOfProcessedData_dict = {'oper':0,
                                        'dble':1,
                                        'test':2,
                                        'research':2,
                                        'unknown':255}

#: Aliases to *typeOfGeneratingProcess* numbers
typeOfGeneratingProcess_dict = {'Analysis':0,
                                'Initialization':1,
                                'Forecast':2,
                                'Bias corrected forecast':3,
                                'Ensemble forecast':4,
                                'Probability forecast':5,
                                'Forecast error':6,
                                'Analysis error':7,
                                'Observation':8,
                                'Climatological':9,
                                'Probability-weighted forecast':10,
                                'Bias-corrected ensemble forecast':11,
                                'Post-processed analysis':12,
                                'Post-processed forecast':13,
                                'Nowcast':14,
                                'Hindcast':15,
                                'unknown':255}

#: Geoid shapes for myproj
myproj_geoid_shapes = {0:{'geoidshape':'sphere',
                          'geoidradius':6367470.},
                       1:{'geoidshape':'sphere',
                          'unit':'m'},
                       2:{'geoidshape':'spheroid',
                          'geoidmajor':6378160.,
                          'geoidminor':6356775.,
                          'flattening':1 / 297.},
                       3:{'geoidshape':'spheroid',
                          'unit':'km'},
                       4:{'geoidshape':'spheroid',
                          'geoidmajor':6378137.,
                          'geoidminor':6356752.314,
                          'flattening':1 / 298.257222101},
                       5:{'geoidshape':'spheroid',
                          'geoidmajor':6378137.,
                          'geoidminor':6356752.3142,
                          'flattening':1 / 298.257223563,
                          'ellps':'WGS84'},
                       6:{'geoidshape':'sphere',
                          'geoidradius':6371229.},
                       7:{'geoidshape':'spheroid',
                          'unit':'m'},
                       8:{'geoidshape':'sphere',
                          'geoidradius':6371200},
                       9:{'geoidshape':'spheroid',
                          'geoidmajor':6377563.369,
                          'geoidminor':6356752.314,
                          'flattening':1 / 299.3249646,
                          'lambda0':0}}

#: Geoid shapes for pyproj
pyproj_geoid_shapes = {0:{'a':6367470.,
                          'b':6367470.},
                       2:{'a':6378160.,
                          # 'b':6356775.,
                          'rf':297.},
                       4:{'ellps':'GRS80',
                          # 'a':6378137.,
                          # 'b':6356752.314,
                          # 'rf':298.257222101
                          },
                       5:{'ellps':'WGS84',
                          # 'a':6378137.,
                          # 'b':6356752.3142,
                          # 'flattening':298.257223563,
                          },
                       6:{'a':6371229.,
                          'b':6371229.},
                       8:{'a':6371200.,
                          'b':6371200.},
                       9:{'ellps':'airy',  # TOBECHECKED:
                          # 'a':6377563.369,
                          # 'b':6356752.314,
                          # 'rf':299.3249646,
                          # 'lambda0':0,
                          }}

#: Type of statistical process over a duration
statistical_processes = {0:'average',
                         1:'accumulation',
                         2:'maximum',
                         3:'minimum',
                         4:'difference',
                         5:'rms',
                         6:'stdev',
                         7:'covariance',
                         8:'-difference',
                         9:'ratio',
                         10:'summation',
                         11:'standardized anomaly'
                         }

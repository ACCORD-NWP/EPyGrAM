#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info

from __future__ import print_function, absolute_import, division, unicode_literals

from unittest import TestCase, skipIf
import tempfile
import os
import cPickle

import epygram

from . import datadir
from .util import suffixes, delta_assertAlmostEqual

basemap_ok = True


# CLASSES
#########
class TestFMT(TestCase):

    datadir = os.path.join(datadir, 'formats')
    basename = ''  # to be redefined in real classes
    len = 0  # to be redefined in real classes

    @property
    def fmt(self):
        return self.__class__.__name__[4:]

    def setUp(self):
        self.filename = os.path.join(self.datadir, self.basename)

    def tearDown(self):
        del self.filename

    def test_listfields(self):
        with epygram.formats.resource(self.filename, 'r', fmt=self.fmt) as r:
            self.assertEqual(len(r.listfields()), self.len)

    def test_what(self):
        with epygram.formats.resource(self.filename, 'r', fmt=self.fmt) as r:
            with open('/dev/null', 'ab') as out:
                r.what(out=out)


class Test_GeometryInterfaces(TestCase):

    datadir = os.path.join(datadir, 'geometries')

    def _test_rwr(self, filename, fid):
        """Generic test Read/Write/Read and check identity."""
        with epygram.formats.resource(filename, 'r') as r:
            f_r = r.readfield(fid)
            tmp = tempfile.mktemp()
            out = epygram.formats.resource(tmp, 'w', fmt=r.format)
            out.writefield(f_r)
            out.close()
            out.open(openmode='r')
            f_rwr = out.readfield(fid)
            out.close()
            os.remove(tmp)
            self.assertEqual(f_r.geometry, f_rwr.geometry,
                             '\n<>\n'.join([str(f_r.geometry),
                                            str(f_rwr.geometry)]))

    def _test_pickled(self, filename, fid, picklename):
        """Generic test whatever the actual domain type."""
        with epygram.formats.resource(filename, 'r') as r:
            fld = r.readfield(fid)
            if isinstance(fld, epygram.base.FieldSet):
                fld = fld[0]
            if not self.update_pickle:
                with open(picklename, 'rb') as pckl:
                    pt_pckld = cPickle.load(pckl)
                self.assertEqual(fld, pt_pckld)
            else:
                with open(picklename, 'wb') as pckl:
                    cPickle.dump(fld, pckl)


class Test_H2DGeometry(Test_GeometryInterfaces):

    fid_to_test = {'FA':'SURFGEOPOTENTIEL',
                   'GRIB1':{'indicatorOfParameter':6},
                   'GRIB2':{'shortName':'orog'},
                   'netCDF':'SURFGEOPOTENTIEL',
                   'LFI':('ZS', 0)}
    fileprefix = ''  # to be defined in real classes

    def _test(self, fmt):
        filename = os.path.join(self.datadir,
                                '.'.join([self.fileprefix,
                                          suffixes[fmt]]))
        self._test_rwr(filename, self.fid_to_test[fmt])


class Test_DDHLFA_Geometry(Test_GeometryInterfaces):

    ddh_domaintype = ['gridpoint', 'zonalband']
    fid_to_test = ''  # will define the actual type of geom to test, Point/V1D
    geom = None  # to be defined in actual classes: point/profile
    update_pickle = False  # set to True if necessary to make a new pickle

    def _test(self, domain):
        filename = os.path.join(self.datadir,
                                '_'.join([self.geom, domain])) + '.ddhlfa'
        picklename = '_'.join([filename, self.fid_to_test]) + '.cPickle'
        self._test_pickled(filename,
                           self.fid_to_test,
                           picklename)


class Test_GeoPoints_WR_fromFA(TestCase):

    fid = 'SURFGEOPOTENTIEL'
    datadir = os.path.join(datadir, 'geometries')
    llv = False

    def _test(self, geom):
        filename = os.path.join(self.datadir,
                                '.'.join([geom, 'fa']))
        with epygram.formats.resource(filename, 'r') as r:
            f = r.readfield(self.fid)
            tmp = tempfile.mktemp()
            out = epygram.formats.resource(tmp, 'w', fmt='GeoPoints',
                                           other_attributes=({'FORMAT':'XYV'} if self.llv else None))
            out.writefield(f, fidkey_for_parameter='FA')
            out.close()
            out.open(openmode='r')
            out.readfield(self.fid)
            out.close()
            os.remove(tmp)


class Test_GeometryMethods(TestCase):

    fid = ''
    basename = ''
    datadir = os.path.join(datadir, 'geometry_methods')
    ij_test = (24, 36)

    def setUp(self):
        filename = os.path.join(self.datadir, self.basename)
        r = epygram.formats.resource(filename, 'r')
        self.fld = r.readfield(self.fid)
        self.geo = self.fld.geometry
        del r

    def tearDown(self):
        del self.fld
        del self.geo

    def assertAlmostEqualSeq(self, first, second, delta):
        self.assertEqual(len(first), len(second))
        for i in range(len(first)):
            self.assertAlmostEqual(first[i], second[i], delta=delta)

    def _test_mtd(self, mtd, args, kwargs, assertion, expected):
        out = getattr(self.geo, mtd)(*args, **kwargs)
        if assertion == 'Equal':
            self.assertEqual(out, expected)
        elif assertion == 'AlmostEqual':
            self.assertAlmostEqual(out, expected, delta=delta_assertAlmostEqual)
        elif assertion == 'IsInstance':
            self.assertIsInstance(out, expected)

    @skipIf(not basemap_ok, 'need basemap module')
    def _test_make_basemap(self, **kwargs):
        from mpl_toolkits.basemap import Basemap
        self.assertIsInstance(self.geo.make_basemap(**kwargs),
                              Basemap)

    def test_make_basemap(self):
        self._test_make_basemap()

    def test_make_basemap_opts1(self):
        self._test_make_basemap(specificproj=('nsper', {}))

    def test_make_basemap_opts2(self):
        self._test_make_basemap(specificproj=('nsper', {'lon':self.point[0],
                                                        'lat':self.point[1],
                                                        'sat_height':600.}))

    def test_make_basemap_opts3(self):
        self._test_make_basemap(specificproj='ortho')

    def test_make_basemap_opts4(self):
        self._test_make_basemap(zoom={'lonmin':self.point[0] - 1,
                                      'lonmax':self.point[0] + 1,
                                      'latmin':self.point[1] - 1,
                                      'latmax':self.point[1] + 1})

    def test_make_point_geometry(self):
        self.assertIsInstance(self.geo.make_point_geometry(*self.point),
                              epygram.geometries.PointGeometry)

    def test_make_profile_geometry(self):
        self.assertIsInstance(self.geo.make_profile_geometry(*self.point),
                              epygram.geometries.V1DGeometry)

    def test_make_section_geometry(self):
        self.assertIsInstance(self.geo.make_section_geometry(*self.transect),
                              epygram.geometries.V2DGeometry)

    def _test_nearest_points(self, request, expected):
        self.assertEqual(self.geo.nearest_points(*self.point, request=request),
                         expected)


class Test_RectGeometryMethods(Test_GeometryMethods):

    def test_point_is_inside_domain_ll(self):
        self.assertTrue(self.geo.point_is_inside_domain_ll(*self.point))
        self.assertFalse(self.geo.point_is_inside_domain_ll(self.point[0],
                                                            - self.point[1]))

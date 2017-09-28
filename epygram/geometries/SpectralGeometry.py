#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
Contains the class that handles spectral parameters and spectral transforms.
"""

from __future__ import print_function, absolute_import, unicode_literals, division

import numpy
import os

from footprints import FootprintBase, FPDict

from epygram import config, epygramError
from epygram.util import RecursiveObject


class SpectralGeometry(RecursiveObject, FootprintBase):
    """Handles the spectral geometry and transforms for a H2DField."""

    _collector = ('geometry',)
    _footprint = dict(
        attr=dict(
            space=dict(
                access='rxx',
                type=str,
                values=['legendre', 'bi-fourier'],
                info='Name of spectral space.'),
            truncation=dict(
                type=FPDict,
                access='rwx',
                info='Handles the spectral truncation parameters.'),
        )
    )

    def __init__(self, *args, **kwargs):
        super(SpectralGeometry, self).__init__(*args, **kwargs)
        self._total_system_memory = (os.sysconf(b'SC_PAGE_SIZE') *
                                     os.sysconf(b'SC_PHYS_PAGES'))  # TODO: use bronx.system.mem_tool
        self._prevent_swapping()

    def _prevent_swapping(self):
        if (self.space == 'legendre' and  # TODO: release condition on space
            (self.needed_memory >=
             config.prevent_swapping_legendre * self._total_system_memory)):
            needed_mem_in_mb = float(self.needed_memory) / (1024 ** 2.)
            total_mem_in_mb = float(self._total_system_memory) / (1024 ** 2.)
            raise epygramError('Legendre spectral transforms need {:.1f} MB \
                                memory, while only {:.1f} MB is available: \
                                SWAPPING prevented !'.format(needed_mem_in_mb,
                                                             total_mem_in_mb))

    @property
    def needed_memory(self):
        """Memory needed for transforms, in bytes."""
        if self.space == 'legendre':  # TODO: release condition on space
            return self.truncation['max'] ** 3 / 2 * 8
        else:
            raise NotImplementedError('space:' + self.space)

    def sp2gp(self, data, gpdims,
              spectral_coeff_order=config.spectral_coeff_order):
        """
        Makes the transform of the spectral data contained in *data* (assumed
        this spectral geometry is that of *data*) to gridpoint space, defined
        by its dimensions contained in *gpdims*, and returns the gridpoint data.

        :param data: spectral data
        :param dict gpdims: gridpoints dimensions
        :param spectral_coeff_order: among 'model' or 'FA',
          cf. default and description in config.spectral_coeff_order

        Input and output data are both 1D.
        """
        from arpifs4py import wtransforms
        self._prevent_swapping()
        if self.space == 'bi-fourier':
            gpdata = wtransforms.w_spec2gpt_lam(gpdims['X'],
                                                gpdims['Y'],
                                                gpdims['X_CIzone'],
                                                gpdims['Y_CIzone'],
                                                self.truncation['in_X'],
                                                self.truncation['in_Y'],
                                                config.KNUMMAXRESOL,
                                                len(data),
                                                False,  # no derivatives
                                                spectral_coeff_order != 'model',
                                                gpdims['X_resolution'],
                                                gpdims['Y_resolution'],
                                                data)[0]
        elif self.space == 'legendre':
            gpdata = wtransforms.w_spec2gpt_gauss(gpdims['lat_number'],
                                                  self.truncation['max'],
                                                  config.KNUMMAXRESOL,
                                                  sum(gpdims['lon_number_by_lat']),
                                                  len(gpdims['lon_number_by_lat']),
                                                  numpy.array(gpdims['lon_number_by_lat']),
                                                  len(data),
                                                  False,  # no derivatives
                                                  spectral_coeff_order != 'model',
                                                  data)[0]
        elif self.space == 'fourier':
            if self.truncation['in_Y'] > 1:
                gpdata = wtransforms.w_spec2gpt_fft1d(len(data),
                                                      self.truncation['in_Y'],
                                                      data,
                                                      gpdims['Y'])
            else:
                gpdata = numpy.ones(gpdims['Y']) * data[0]
        else:
            raise epygramError("unknown spectral space:" + self.space + ".")
        return gpdata

    def gp2sp(self, data, gpdims,
              spectral_coeff_order=config.spectral_coeff_order):
        """
        Makes the transform of the gridpoint data contained in *data*
        to the spectral space and truncation of this object, and returns
        the spectral data.

        :param data: gridpoint data
        :param dict gpdims: gridpoints dimensions
        :param spectral_coeff_order: among 'model' or 'FA',
          cf. default and description in config.spectral_coeff_order

        Input and output data are both 1D.
        """
        from arpifs4py import wtransforms
        self._prevent_swapping()
        if self.space == 'bi-fourier':
            SPdatasize = wtransforms.w_etrans_inq(gpdims['X'],
                                                  gpdims['Y'],
                                                  gpdims['X_CIzone'],
                                                  gpdims['Y_CIzone'],
                                                  self.truncation['in_X'],
                                                  self.truncation['in_Y'],
                                                  config.KNUMMAXRESOL,
                                                  gpdims['X_resolution'],
                                                  gpdims['Y_resolution'])[1]
            spdata = wtransforms.w_gpt2spec_lam(SPdatasize,
                                                gpdims['X'],
                                                gpdims['Y'],
                                                gpdims['X_CIzone'],
                                                gpdims['Y_CIzone'],
                                                self.truncation['in_X'],
                                                self.truncation['in_Y'],
                                                config.KNUMMAXRESOL,
                                                gpdims['X_resolution'],
                                                gpdims['Y_resolution'],
                                                spectral_coeff_order != 'model',
                                                data)
        elif self.space == 'legendre':
            SPdatasize = wtransforms.w_trans_inq(gpdims['lat_number'],
                                                 self.truncation['max'],
                                                 len(gpdims['lon_number_by_lat']),
                                                 numpy.array(gpdims['lon_number_by_lat']),
                                                 config.KNUMMAXRESOL)[1]
            SPdatasize *= 2  # complex coefficients
            spdata = wtransforms.w_gpt2spec_gauss(SPdatasize,
                                                  gpdims['lat_number'],
                                                  self.truncation['max'],
                                                  config.KNUMMAXRESOL,
                                                  len(gpdims['lon_number_by_lat']),
                                                  numpy.array(gpdims['lon_number_by_lat']),
                                                  len(data),
                                                  spectral_coeff_order != 'model',
                                                  data)
        elif self.space == 'fourier':
            # 1D case
            SPdatasize = wtransforms.w_etrans_inq(gpdims['X'], gpdims['Y'],
                                                  gpdims['X_CIzone'],
                                                  gpdims['Y_CIzone'],
                                                  self.truncation['in_X'],
                                                  self.truncation['in_Y'],
                                                  config.KNUMMAXRESOL,
                                                  gpdims['X_resolution'],
                                                  gpdims['Y_resolution'])[1]
            if self.truncation['in_Y'] <= 1:
                spdata = numpy.zeros(SPdatasize)
                spdata[0] = data[0]
            else:
                raise NotImplementedError("direct transform for 1D fourier" +
                                          " transform.")
        else:
            raise epygramError("unknown spectral space:" + self.space + ".")
        return spdata

    def compute_xy_spderivatives(self, data, gpdims,
                                 spectral_coeff_order=config.spectral_coeff_order):
        """
        Compute the derivatives of the spectral data contained in *data* in
        spectral space (assumed this spectral geometry is that of *data*) and
        return it in gridpoint space, defined by its dimensions contained in
        *gpdims*.

        :param data: spectral data
        :param dict gpdims: gridpoints dimensions
        :param spectral_coeff_order: among 'model' or 'FA',
          cf. default and description in config.spectral_coeff_order

        Returns: (dz/dx, dz/dy)

        Input and output data are both 1D.
        """
        from arpifs4py import wtransforms
        self._prevent_swapping()
        if self.space == 'bi-fourier':
            gpdata = wtransforms.w_spec2gpt_lam(gpdims['X'],
                                                gpdims['Y'],
                                                gpdims['X_CIzone'],
                                                gpdims['Y_CIzone'],
                                                self.truncation['in_X'],
                                                self.truncation['in_Y'],
                                                config.KNUMMAXRESOL,
                                                len(data),
                                                True,  # derivatives
                                                spectral_coeff_order != 'model',
                                                gpdims['X_resolution'],
                                                gpdims['Y_resolution'],
                                                data)
            gpdata = (gpdata[2], gpdata[1])
        elif self.space == 'legendre':
            gpdata = wtransforms.w_spec2gpt_gauss(gpdims['lat_number'],
                                                  self.truncation['max'],
                                                  config.KNUMMAXRESOL,
                                                  sum(gpdims['lon_number_by_lat']),
                                                  len(gpdims['lon_number_by_lat']),
                                                  numpy.array(gpdims['lon_number_by_lat']),
                                                  len(data),
                                                  True,  # derivatives
                                                  spectral_coeff_order != 'model',
                                                  data)
            gpdata = (gpdata[2], gpdata[1])
        elif self.space == 'fourier':
            raise NotImplementedError('fourier(1D): not yet !')
        else:
            raise epygramError("unknown spectral space:" + self.space + ".")
        return gpdata

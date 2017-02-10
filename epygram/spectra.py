#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info
"""
This module contains:

- a class to handle variance spectrum;
- a function to compute DCT spectrum from a 2D field;
- a function to sort spectra with regards to their name;
- a function to plot a series of spectra.
"""

from __future__ import print_function, absolute_import, unicode_literals, division

import numpy
import copy

from epygram import epygramError
from epygram.util import RecursiveObject, write_formatted_table, set_figax


_file_id = 'epygram.spectra.Spectrum'
_file_columns = ['#', 'lambda', 'variance']


def read_Spectrum(filename):
    """Read a Spectrum written in file and return it."""

    with open(filename, 'r') as _file:
        init_kwargs = {}
        # Spectrum file id
        assert _file.readline()[:-1] == _file_id, \
               ' '.join(["file:", filename, "does not contain a Spectrum."])
        # header: other stuff
        line = _file.readline()[:-1]
        while line[0] != '#':
            init_kwargs[line.split('=')[0].strip()] = line.split('=')[1].strip()
            line = _file.readline()[:-1]
        # columns description
        assert line.split() == _file_columns
        # values
        table = [line.split() for line in _file.readlines()]
        if int(table[0][0]) == 0:
            init_kwargs['mean2'] = float(table.pop(0)[2])
        elif not int(table[0][0]) == 1:
            raise epygramError("first wavenumber must be 0 or 1.")
        if 'resolution' in init_kwargs:
            init_kwargs['resolution'] = float(init_kwargs['resolution'])
        else:
            k = int(table[-1][0])
            init_kwargs['resolution'] = float(table[-1][1]) * k / (2 * (k + 1))
        variances = [float(line[2]) for line in table]

        return Spectrum(variances, **init_kwargs)


class Spectrum(RecursiveObject):
    """
    A spectrum can be seen as a quantification of a signal's variance with
    regards to scale.
    If the signal is defined in physical space on N points, its spectral
    representation will be a squared mean value (wavenumber 0) and variances for
    N-1 wavenumbers.
    For details and documentation, see
        Denis et al. (2002) : 'Spectral Decomposition of Two-Dimensional
        Atmospheric Fields on Limited-Area Domains
        Using the Discrete Cosine Transform (DCT)'
    """

    def __init__(self, variances, name=None, resolution=None, mean2=None,
                 **kwargs):
        """
        Args:
        - *variances* being the variances of the spectrum, from wavenumber 1
          to N-1.
        - *name* is an optional name for the spectrum.
        - *resolution* is an optional resolution for the field represented by
          the spectrum. It is used to compute the according wavelengths.
          Resolution unit is arbitrary, to the will of the user.
        - *mean2* is the optional mean^2 of the field, i.e. variance of
          wavenumber 0 of the spectrum.
        """

        self.variances = numpy.array(variances)
        self.name = name
        self.resolution = resolution
        self.mean2 = mean2
        for k, v in kwargs.items():
            setattr(self, k, v)

    @property
    def wavenumbers(self):
        """Gets the wavenumbers of the spectrum."""
        return numpy.arange(1, len(self.variances) + 1)

    @property
    def wavelengths(self):
        """Gets the wavelengths of the spectrum."""
        K = len(self.variances) + 1
        return numpy.array([2. * self.resolution * K / k
                            for k in self.wavenumbers])

    def write(self, out):
        """
        Writes the spectrum with formatted output in *out*.

        *out* must be an output open file-like object
        (*out*.write() only is needed).
        """

        out.write(_file_id + '\n')
        if self.name is not None:
            out.write('name = ' + str(self.name) + '\n')
        if self.resolution is not None:
            out.write('resolution = ' + str(self.resolution) + '\n')
        table = [_file_columns,
                 [0, '-', self.mean2]]
        wn = self.wavenumbers
        wl = self.wavelengths
        var = self.variances
        for k in range(len(var)):
            table.append([wn[k], wl[k], var[k]])
        write_formatted_table(out, table)

    def dump(self, filename):
        """
        Writes the spectrum with formatted output in *filename*.
        """
        with open(filename, 'w') as _file:
            self.write(_file)

    def plotspectrum(self,
                     over=(None, None),
                     slopes=[{'exp':-3, 'offset':1, 'label':'-3'},
                             {'exp':-5. / 3., 'offset':1, 'label':'-5/3'}],
                     zoom=None,
                     unit='SI',
                     title=None):
        """
        Plot the spectrum.
        Cf. function plotspectra() of this module for arguments.
        """

        return plotspectra(self,
                           over=over,
                           slopes=slopes,
                           zoom=zoom,
                           unit=unit,
                           title=title)

##########
# internal
    def _check_operands(self, other):
        """Check compatibility of both spectra."""

        if isinstance(other, Spectrum):
            assert all((len(self.variances) == len(other.variances),
                        self.resolution == other.resolution or
                        None in (self.resolution, other.resolution))), \
                   "operations between spectra require that they share dimension and resolution."
        else:
            try:
                _ = float(other)
            except (ValueError, TypeError) as e:
                raise type(e)('*other* must be a Spectrum or a float-convertible.')
        if isinstance(other, Spectrum):
            othermean2 = other.mean2
            othername = other.name
            otherval = other.variances
        else:
            othermean2 = other
            othername = str(other)
            otherval = other

        return (othermean2, othername, otherval)

    def __add__(self, other):
        (othermean2, othername, otherval) = self._check_operands(other)
        mean2 = None if None in (self.mean2, othermean2) else self.mean2 + othermean2
        name = None if (self.name is othername is None) else str(self.name) + '+' + str(othername)
        return Spectrum(self.variances + otherval,
                        name=name,
                        resolution=self.resolution,
                        mean2=mean2)

    def __sub__(self, other):
        (othermean2, othername, otherval) = self._check_operands(other)
        mean2 = None if None in (self.mean2, othermean2) else self.mean2 - othermean2
        name = None if (self.name is othername is None) else str(self.name) + '+' + str(othername)
        return Spectrum(self.variances - otherval,
                        name=name,
                        resolution=self.resolution,
                        mean2=mean2)

    def __mul__(self, other):
        (othermean2, othername, otherval) = self._check_operands(other)
        mean2 = None if None in (self.mean2, othermean2) else self.mean2 * othermean2
        name = None if (self.name is othername is None) else str(self.name) + '+' + str(othername)
        return Spectrum(self.variances * otherval,
                        name=name,
                        resolution=self.resolution,
                        mean2=mean2)

    def __div__(self, other):
        (othermean2, othername, otherval) = self._check_operands(other)
        mean2 = None if None in (self.mean2, othermean2) else self.mean2 / othermean2
        name = None if (self.name is othername is None) else str(self.name) + '+' + str(othername)
        return Spectrum(self.variances / otherval,
                        name=name,
                        resolution=self.resolution,
                        mean2=mean2)


#############################
### FUNCTIONS FOR SPECTRA ###
#############################
def sort(spectra):
    """ Sort a list of spectra with regards to their name. """

    untied_spectra = copy.copy(spectra)
    sortedspectra = []
    for f in sorted([s.name for s in untied_spectra], reverse=True):
        for s in range(len(untied_spectra)):
            if untied_spectra[s].name == f:
                sortedspectra.append(untied_spectra.pop(s))
                break
    return sortedspectra


def dctspectrum(x, log=None, verbose=False):
    """
    Function *dctspectrum* takes a 2D-array as argument and returns its 1D
    DCT ellipse spectrum.

    For details and documentation, see
        Denis et al. (2002) : 'Spectral Decomposition of Two-Dimensional
        Atmospheric Fields on Limited-Area Domains Using
        the Discrete Cosine Transform (DCT).'

    *log* is an optional logging.Logger instance to which print info
    in *verbose* case.
    """
    import scipy.fftpack as tfm

    # compute transform
    if log is not None and verbose:
        log.info("dctspectrum: compute DCT transform...")
    norm = 'ortho'  # None
    y = tfm.dct(tfm.dct(x, norm=norm, axis=0), norm=norm, axis=1)

    # compute spectrum
    if log is not None and verbose:
        log.info("dctspectrum: compute variance spectrum...")
    N, M = y.shape
    N2 = N ** 2
    M2 = M ** 2
    MN = M * N
    K = min(M, N)
    variance = numpy.zeros(K)
    variance[0] = y[0, 0] ** 2 / MN
    for j in range(0, N):
        j2 = float(j) ** 2
        for i in range(0, M):
            var = y[j, i] ** 2 / MN
            k = numpy.sqrt(float(i) ** 2 / M2 + j2 / N2) * K
            k_inf = int(numpy.floor(k))
            k_sup = k_inf + 1
            weightsup = k - k_inf
            weightinf = 1.0 - weightsup
            if 0 <= k < 1:
                variance[1] += weightsup * var
            if 1 <= k < K - 1:
                variance[k_inf] += weightinf * var
                variance[k_sup] += weightsup * var
            if K - 1 <= k < K:
                variance[k_inf] += weightinf * var

    return variance


def plotspectra(spectra,
                over=(None, None),
                slopes=[{'exp':-3, 'offset':1, 'label':'-3'},
                        {'exp':-5. / 3., 'offset':1, 'label':'-5/3'}],
                zoom=None,
                unit='SI',
                title=None):
    """
    To plot a series of spectra.

    Args:\n
    - over = any existing figure and/or ax to be used for the
      plot, given as a tuple (fig, ax), with None for
      missing objects. *fig* is the frame of the
      matplotlib figure, containing eventually several
      subplots (axes); *ax* is the matplotlib axes on
      which the drawing is done. When given (is not None),
      these objects must be coherent, i.e. ax being one of
      the fig axes.
    - spectra = a Spectrum instance or a list of.
    - unit: string accepting LaTeX-mathematical syntaxes
    - slopes = list of dict(
      - exp=x where x is exposant of a A*k**-x slope
      - offset=A where A is logscale offset in a A*k**-x slope;
        a offset=1 is fitted to intercept the first spectra at wavenumber = 2
      - label=(optional label) appearing 'k = label' in legend)
    - zoom = dict(xmin=,xmax=,ymin=,ymax=)
    - title = string for title
    """
    import matplotlib.pyplot as plt
    plt.rc('font', family='serif')

    fig, ax = set_figax(*over)

    if isinstance(spectra, Spectrum):
        spectra = [spectra]
    # prepare dimensions
    window = dict()
    window['ymin'] = min([min(s.variances) for s in spectra]) / 10
    window['ymax'] = max([max(s.variances) for s in spectra]) * 10
    window['xmax'] = max([max(s.wavelengths) for s in spectra]) * 1.5
    window['xmin'] = min([min(s.wavelengths) for s in spectra]) * 0.8
    if zoom is not None:
        for k, v in zoom.items():
            window[k] = v
    x1 = window['xmax']
    x2 = window['xmin']

    # colors and linestyles
    colors = ['red', 'blue', 'green', 'orange', 'magenta', 'darkolivegreen',
              'yellow', 'salmon', 'black']
    linestyles = ['-', '--', '-.', ':']

    # axes
    if title is not None :
        ax.set_title(title)
    ax.set_yscale('log')
    ax.set_ylim(window['ymin'], window['ymax'])
    ax.set_xscale('log')
    ax.set_xlim(window['xmax'], window['xmin'])
    ax.grid()
    ax.set_xlabel('wavelength ($km$)')
    ax.set_ylabel(r'variance spectrum ($' + unit + '$)')

    # plot slopes
    # we take the second wavenumber (of first spectrum) as intercept, because
    # it is often better fitted with spectrum than the first one
    x_intercept = spectra[0].wavelengths[1]
    y_intercept = spectra[0].variances[1]
    i = 0
    for slope in slopes:
        # a slope is defined by y = A * k**-s and we plot it with
        # two points y1, y2
        try:
            label = slope['label']
        except KeyError:
            # label = str(Fraction(slope['exp']).limit_denominator(10))
            label = str(slope['exp'])
        # because we plot w/r to wavelength, as opposed to wavenumber
        s = -slope['exp']
        A = y_intercept * x_intercept ** (-s) * slope['offset']
        y1 = A * x1 ** s
        y2 = A * x2 ** s
        ax.plot([x1, x2], [y1, y2], color='0.7',
                linestyle=linestyles[i % len(linestyles)],
                label=r'$k^{' + label + '}$')
        i += 1

    # plot spectra
    i = 0
    for s in spectra:
        ax.plot(s.wavelengths, s.variances, color=colors[i % len(colors)],
                linestyle=linestyles[i // len(colors)], label=s.name)
        i += 1

    # legend
    legend = ax.legend(loc='lower left', shadow=True)
    for label in legend.get_texts():
        label.set_fontsize('medium')

    return (fig, ax)

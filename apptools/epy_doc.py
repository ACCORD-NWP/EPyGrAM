#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) Météo France (2014-)
# This software is governed by the CeCILL-C license under French law.
# http://www.cecill.info

from __future__ import print_function, unicode_literals

import argparse
import os
import webbrowser

import epygram

if __name__ == '__main__':

    # 1. Parse arguments
    ####################
    parser = argparse.ArgumentParser(description='A command to open the local epygram documentation.',
                                     epilog='End of help for: %(prog)s (EPyGrAM v' + epygram.__version__ + ')')
    parser.add_argument('-o', '--open',
                        dest='open',
                        default=False,
                        help="open the epygram doc in a web browser.",
                        action='store_true',
                        )
    parser.add_argument('-s', '--search',
                        dest='search',
                        default=None,
                        help="search for the given keyword in the doc search engine (needs -o).",
                        )
    args = parser.parse_args()

    # 2. Initializations
    ####################
    url = os.path.join(os.path.realpath(os.path.dirname(epygram.__file__)),
                       os.path.join('doc_sphinx', 'html', 'index.html'))
    url = 'file://' + url

    # 3. Main
    #########
    print("#" * 80)
    print('Epygram doc url = ' + url )
    if args.open:
        if args.search is not None:
            url = url.replace('index.html', 'search.html?q={}'.format(args.search))  # secure ?
        webbrowser.open(url)
    else:
        print('To open it, run again this command with option -o')
    print("#" * 80)

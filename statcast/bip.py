import os

import pandas as pd
import numpy as np

from .database.bbsavant import DB as SavantDB
from .database.gd_weather import DB as WeatherDB

from .better.randomforest import TreeSelectingRFRegressor
from .better.mixed import BetterLME4
from .better.utils import findTrainSplit, otherRFE

from . import __path__


savantDB = SavantDB('fast')
weatherDB = WeatherDB('fast')
weatherData = pd.read_sql_query(
    '''SELECT *
    FROM {}'''.format(weatherDB._tblName), weatherDB.engine)

_storagePath = os.path.join(__path__[0], 'Storage')

_scImputer = \
    TreeSelectingRFRegressor(xLabels=['start_speed',
                                      'x0',
                                      'z0',
                                      'events',
                                      'zone',
                                      'hit_location',
                                      'bb_type',
                                      'balls',
                                      'strikes',
                                      'pfx_x',
                                      'pfx_z',
                                      'px',
                                      'pz',
                                      'hc_x',
                                      'hc_y',
                                      'vx0',
                                      'vy0',
                                      'vz0',
                                      'effective_speed',
                                      'sprayAngle',
                                      'hitDistanceGD'],
                             yLabels=['hit_speed',
                                      'hit_angle',
                                      'hit_distance_sc'],
                             oob_score=True,
                             n_jobs=-1)
_scFactorMdl = \
    BetterLME4(xLabels=['batter', 'pitcher', 'gdTemp', 'home_team'],
               yLabels=['hit_speed', 'hit_angle', 'hit_distance_sc'],
               formulas='(1|batter) + (1|pitcher) + gdTemp + '
               '(1|home_team)')


class Bip():
    '''Doc String'''

    def __init__(self, years, scImputerName=None, scFactorMdlName=None,
                 n_jobs=-1):
        '''Doc String'''

        self.n_jobs = n_jobs
        self.years = years

        self._initData(years)
        self.totalData = self.data
        self.data = self.data.sample(frac=0.2)  # TAKE THIS OUT LATER!!!

        self._initSCImputer(scImputerName=scImputerName)
        self._imputeSCData()

        self._initSCFactorMdl(scFactorMdlName=scFactorMdlName)
        self._createSCFactorMdl()

    def _initData(self, years):
        '''Doc String'''

        self.data = pd.DataFrame()
        for year in years:
            rawD = pd.read_sql_query(
                '''SELECT *
                FROM {}
                WHERE type = 'X'
                AND game_year = {}
                AND game_type = 'R ' '''.format(savantDB._tblName, year),
                savantDB.engine)
            self.data = self.data.append(rawD, ignore_index=True)

        self.data['sprayAngle'] = \
            (np.arctan2(208 - self.data.hc_y, self.data.hc_x - 128) /
             (2 * np.pi) * 360 + 90) % 360 - 180
        self.data['hitDistanceGD'] = np.sqrt((self.data.hc_x - 128) ** 2 +
                                             (208 - self.data.hc_y) ** 2)

        self.data[['on_3b', 'on_2b', 'on_1b']] = \
            self.data[['on_3b', 'on_2b', 'on_1b']]. \
            fillna(value=0).astype('int')
        self.data['baseState'] = \
            (self.data[['on_3b', 'on_2b', 'on_1b']] == 0). \
            replace([True, False], ['_', 'X']).sum(axis=1)

        temps = pd.Series(weatherData.temp.values, index=weatherData.game_pk)
        temps = temps[~temps.index.duplicated(keep='first')]
        self.data['gdTemp'] = temps.loc[self.data.game_pk].values

        excludeEvents = ['Batter Interference', 'Hit By Pitch', 'Strikeout',
                         'Walk', 'Fan Intereference', 'Field Error',
                         'Catcher Interference', 'Fan interference']
        self.data['exclude'] = self.data.events.isin(excludeEvents)

        categories = ['pitch_type', 'batter', 'pitcher', 'events', 'zone',
                      'stand', 'p_throws', 'home_team', 'away_team',
                      'hit_location', 'bb_type', 'on_3b', 'on_2b', 'on_1b',
                      'inning_topbot', 'catcher', 'umpire', 'game_pk',
                      'baseState']
        for category in categories:
            self.data[category] = self.data[category].astype('category')

        zeroIsMissingCols = ['hit_speed', 'hit_angle', 'hit_distance_sc']
        for col in zeroIsMissingCols:
            self.data.loc[self.data[col] == 0, col] = np.nan

        self.data['missing'] = [', '.join(self.data.columns[row])
                                for row in self.data.isnull().values]

        self.data.fillna(self.data.median(), inplace=True)
        return

    def _imputeSCData(self):
        '''Doc String'''

        imputed = self.imputed(_scImputer.yLabels)
        imputeData = self.data[~self.data.exclude & imputed]
        imputeY = pd.DataFrame(self.scImputer.predictD(imputeData),
                               columns=self.scImputer.yLabels)

        for label in self.scImputer.yLabels:
            imputeThisCol = self.data.missing.map(lambda x: label in x)
            self.data.loc[~self.data.exclude & imputeThisCol, label] = \
                imputeY.loc[imputeThisCol[~self.data.exclude & imputed].values,
                            label].values

        return

    def _initSCImputer(self, scImputerName=None):
        '''Doc String'''

        if scImputerName == 'new':
            self._createSCImputer()
        elif scImputerName:
            self.scImputer = _scImputer.load(scImputerName)
        else:
            name = 'scImputer{}'.format('_'.join(str(year)
                                                 for year in self.years))
            try:
                self.scImputer = \
                    _scImputer.load(name=name, searchDirs=(_storagePath,))
            except FileNotFoundError:
                self._createSCImputer()
                self.scImputer.name = name
                self.scImputer.save(os.path.join(_storagePath,
                                                 self.scImputer.name))

    def _createSCImputer(self):
        '''Doc String'''

        imputed = self.imputed(_scImputer.yLabels)
        trainData = self.data[~self.data.exclude & ~imputed]
        self.scImputer = findTrainSplit(_scImputer, trainData,
                                        n_jobs=self.n_jobs)
        subTrainData = trainData.loc[self.scImputer.trainX_.index, :]
        self.scImputer = otherRFE(self.scImputer, subTrainData, cv=10,
                                  n_jobs=self.n_jobs)
        self.scImputer = findTrainSplit(self.scImputer, trainData, cv=10,
                                        n_jobs=self.n_jobs)

    def _initSCFactorMdl(self, scFactorMdlName=None):
        '''Doc String'''

        if scFactorMdlName == 'new':
            self._createSCFactorMdl()
        elif scFactorMdlName:
            self.scFactorMdl = _scFactorMdl.load(scFactorMdlName)
        else:
            try:
                name = 'scFactorMdl{}'.format('_'.join(str(year)
                                                       for year in self.years))
                self.scFactorMdl = \
                    _scFactorMdl.load(name=name, searchDirs=(_storagePath,))
            except FileNotFoundError:
                self._createSCFactorMdl()
                self.scFactorMdl.save(os.path.join(_storagePath,
                                                   self.scFactorMdl.name))

    def _createSCFactorMdl(self):
        '''Doc String'''

        self.scFactorMdl = _scFactorMdl

    def imputed(self, columns):
        '''Doc String'''

        return self.data.missing.map(lambda x:
                                     any(y in x
                                         for y in columns))

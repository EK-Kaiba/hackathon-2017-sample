# coding: utf-8

import copy
import os

import brica1.gym
import numpy as np
import six.moves.cPickle as pickle
# from chainer import cuda

from ml.cnn_feature_extractor import CnnFeatureExtractor
from ml.q_net import QNet
from ml.experience import Experience

use_gpu = int(os.getenv('GPU', '-1'))

class VVCComponent(brica1.Component):
    image_feature_count = 1
    cnn_feature_extractor = 'alexnet_feature_extractor.pickle'
    model = 'bvlc_alexnet.caffemodel'
    model_type = 'alexnet'
    image_feature_dim = 256 * 6 * 6

    def __init__(self, n_output=10240, n_input=1):
        # image_feature_count = 1
        super(VVCComponent, self).__init__()

        self.use_gpu = use_gpu
        self.n_output = n_output
        self.n_input = n_input

    def set_model(self, feature_extractor):
        self.feature_extractor = feature_extractor

    def load_model(self, cnn_feature_extractor):
        if os.path.exists(cnn_feature_extractor):
            print("loading... " + cnn_feature_extractor),
            self.feature_extractor = pickle.load(open(cnn_feature_extractor))
            print("done")
        else:
            self.feature_extractor = CnnFeatureExtractor(self.use_gpu, self.model, self.model_type,
                                                         self.image_feature_dim)
            pickle.dump(self.feature_extractor, open(cnn_feature_extractor, 'w'))
            print("pickle.dump finished")

    def _observation_to_featurevec(self, observation):
        # TODO clean
        if self.image_feature_count == 1:
            return np.r_[self.feature_extractor.feature(observation["image"][0]),
                         observation["depth"][0]]
        elif self.image_feature_count == 4:
            return np.r_[self.feature_extractor.feature(observation["image"][0]),
                         self.feature_extractor.feature(observation["image"][1]),
                         self.feature_extractor.feature(observation["image"][2]),
                         self.feature_extractor.feature(observation["image"][3]),
                         observation["depth"][0],
                         observation["depth"][1],
                         observation["depth"][2],
                         observation["depth"][3]]
        else:
            print("not supported: number of camera")

    def fire(self):
        observation = self.get_in_port('Isocortex#V1-Isocortex#VVC-Input').buffer
        obs_array = self._observation_to_featurevec(observation)

        self.results['Isocortex#VVC-BG-Output'] = obs_array
        self.results['Isocortex#VVC-UB-Output'] = obs_array


class BGComponent(brica1.Component):
    def __init__(self, n_input=10240, n_output=1):
        super(BGComponent, self).__init__()
        self.use_gpu = use_gpu
        self.epsilon = 1.0
        actions = [0, 1, 2]
        epsilon_delta = 1.0 / 10 ** 4.4
        min_eps = 0.1
        self.input_dim = n_input
        self.q_net = QNet(self.use_gpu, actions, self.input_dim, self.epsilon, epsilon_delta, min_eps)

    def start(self):
        features = self.get_in_port('Isocortex#VVC-BG-Input').buffer
        action = self.q_net.start(features)
        return action

    def end(self, reward):  # Episode Terminated
        print('episode finished. Reward:%.1f / Epsilon:%.6f' % (reward, self.epsilon))
        self.replayed_experience = self.get_in_port('UB-BG-Input').buffer
        self.q_net.update_model(self.replayed_experience)

    def fire(self):
        reward = self.get_in_port('RB-BG-Input').buffer
        features = self.get_in_port('Isocortex#VVC-BG-Input').buffer
        self.replayed_experience = self.get_in_port('UB-BG-Input').buffer

        action, eps, q_max = self.q_net.step(features)
        time = self.q_net.update_model(self.replayed_experience)

        print('Step:%d  Action:%d  Reward:%.1f  Epsilon:%.6f  Q_max:%3f' % (
            time, self.q_net.action_to_index(action), reward, eps, q_max))

        self.epsilon = eps
        self.results['BG-Isocortex#FL-Output'] = np.array([action])


class UBComponent(brica1.Component):
    def __init__(self):
        super(UBComponent, self).__init__()
        use_gpu = 0
        data_size = 10**5
        replay_size = 32
        hist_size = 1
        initial_exploration = 10**3
        dim = 10240
        self.experience = Experience(use_gpu=use_gpu, data_size=data_size, replay_size=replay_size,
                            hist_size=hist_size, initial_exploration=initial_exploration, dim=dim)
        vvc_input = np.zeros((hist_size, dim), dtype=np.uint8)
        self.last_state = vvc_input
        self.state = vvc_input
        self.time = 0

    def end(self, action, reward):
        self.time += 1
        replay_start, s_replay, a_replay, r_replay, s_dash_replay, episode_end_replay = \
            self.experience.end_episode(self.time, self.last_state, action, reward)
        self.results['UB-BG-Output'] = [replay_start, s_replay, a_replay, r_replay, s_dash_replay, episode_end_replay]

    def fire(self):
        self.state = self.get_in_port('Isocortex#VVC-UB-Input').buffer
        action, reward = self.get_in_port('Isocortex#FL-UB-Input').buffer
        self.experience.stock(self.time, self.last_state, action, reward, self.state, False)
        replay_start, s_replay, a_replay, r_replay, s_dash_replay, episode_end_replay = \
            self.experience.replay(self.time)

        self.results['UB-BG-Output'] = [replay_start, s_replay, a_replay, r_replay, s_dash_replay, episode_end_replay]
        self.last_state = self.state.copy()
        self.time += 1


class FLComponent(brica1.Component):
    def __init__(self):
        super(FLComponent, self).__init__()
        self.last_action = np.array([0])

    def fire(self):
        action = self.get_in_port('BG-Isocortex#FL-Input').buffer
        reward = self.get_in_port('RB-Isocortex#FL-Input').buffer
        self.results['Isocortex#FL-MO-Output'] = action
        self.results['Isocortex#FL-UB-Output'] = [self.last_action, reward]

        self.last_action = action

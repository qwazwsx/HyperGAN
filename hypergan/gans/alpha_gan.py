import importlib
import json
import numpy as np
import os
import sys
import time
import uuid
import copy

from hypergan.discriminators import *
from hypergan.encoders import *
from hypergan.generators import *
from hypergan.inputs import *
from hypergan.samplers import *
from hypergan.trainers import *

import hyperchamber as hc
from hyperchamber import Config
from hypergan.ops import TensorflowOps
import tensorflow as tf
import hypergan as hg

from hypergan.gan_component import ValidationException, GANComponent
from .base_gan import BaseGAN

from hypergan.discriminators.fully_connected_discriminator import FullyConnectedDiscriminator
from hypergan.encoders.uniform_encoder import UniformEncoder
from hypergan.trainers.multi_step_trainer import MultiStepTrainer

class AlphaGAN(BaseGAN):
    """ 
    """
    def __init__(self, *args, **kwargs):
        BaseGAN.__init__(self, *args, **kwargs)
        self.discriminator = None
        self.encoder = None
        self.generator = None
        self.loss = None
        self.trainer = None
        self.session = None


    def required(self):
        return "generator".split()

    def create(self):
        BaseGAN.create(self)
        if self.session is None: 
            self.session = self.ops.new_session(self.ops_config)
        with tf.device(self.device):
            config = self.config
            ops = self.ops

            d2 = dict(config.discriminator)
            d2['class'] = self.ops.lookup("class:hypergan.discriminators.pyramid_discriminator.PyramidDiscriminator")
            encoder = self.create_component(d2)
            encoder.ops.describe("encoder")
            encoder.create(self.inputs.x)
            encoder.z = tf.zeros(0)

            d3 = dict(config.discriminator)
            d3["layers"]=0
            d3["extra_layers"]=4
            d3["extra_layers_reduction"]=2
            d3["fc_layer_size"]=512
            d3["fc_layers"]=0
            d3['layer_filter']=None

            encoder_discriminator = self.create_component(d3)
            #encoder_discriminator = FullyConnectedDiscriminator(self, {})
            encoder_discriminator.ops.describe("encoder_discriminator")
            standard_discriminator = self.create_component(config.discriminator)
            standard_discriminator.ops.describe("discriminator")

            #encoder.sample = ops.reshape(encoder.sample, [ops.shape(encoder.sample)[0], -1])
            uniform_encoder_config = config.encoder
            z_size = 1
            for size in ops.shape(encoder.sample)[1:]:
                z_size *= size
            uniform_encoder_config.z = z_size
            uniform_encoder = UniformEncoder(self, uniform_encoder_config)
            uniform_encoder.create()

            self.generator = self.create_component(config.generator)

            z = uniform_encoder.sample
            x = self.inputs.x
            # TODO copy paste
            projection_input = ops.reshape(encoder.sample, [ops.shape(encoder.sample)[0],-1])
            projections = []
            for projection in uniform_encoder.config.projections:
                projection = uniform_encoder.lookup(projection)(uniform_encoder.config, self.gan, projection_input)
                projection = ops.reshape(projection, ops.shape(encoder.sample))
                projections.append(projection)
            z_hat = tf.concat(axis=3, values=projections)
            print("_Z", z_hat, z)

            z = ops.reshape(z, ops.shape(z_hat))
            # end encoding
            g = self.generator.create(z)
            sample = self.generator.sample
            print("Z, Z_HAT", z, z_hat)
            x_hat = self.generator.reuse(z_hat)

            encoder_discriminator.create(x=z, g=z_hat)

            eloss = dict(config.loss)
            eloss['gradient_penalty'] = False
            encoder_loss = self.create_component(eloss, discriminator = encoder_discriminator)
            encoder_loss.create()

            stacked_xg = ops.concat([x, x_hat, g], axis=0)
            standard_discriminator.create(stacked_xg)

            standard_loss = self.create_component(config.loss, discriminator = standard_discriminator)
            standard_loss.create(split=3)

            self.trainer = self.create_component(config.trainer)

            #loss terms
            distance = config.distance or ops.lookup('l1_distance')
            cycloss = tf.reduce_mean(distance(self.inputs.x,x_hat))
            cycloss_lambda = config.cycloss_lambda or 10
            cycloss *= cycloss_lambda
            print("CYLAMB", cycloss_lambda)
            if cycloss_lambda > 0:
                loss1=('generator', cycloss + encoder_loss.g_loss)
                loss2=('generator', cycloss + standard_loss.g_loss)
            else:
                loss1=('generator', encoder_loss.g_loss)
                loss2=('generator', standard_loss.g_loss)
            loss3=('discriminator', standard_loss.d_loss)
            loss4=('discriminator', encoder_loss.d_loss)

            var_lists = []
            var_lists.append(encoder.variables())
            var_lists.append(self.generator.variables())
            var_lists.append(standard_discriminator.variables())
            var_lists.append(encoder_discriminator.variables())

            metrics = []
            metrics.append(encoder_loss.metrics)
            metrics.append(standard_loss.metrics)
            metrics.append(None)
            metrics.append(None)

            # trainer

            self.trainer = MultiStepTrainer(self, self.config.trainer, [loss1,loss2,loss3,loss4], var_lists=var_lists, metrics=metrics)
            self.trainer.create()

            self.session.run(tf.global_variables_initializer())

            self.generator.sample = sample
            self.encoder = encoder
            self.uniform_encoder = uniform_encoder


    def step(self, feed_dict={}):
        return self.trainer.step(feed_dict)

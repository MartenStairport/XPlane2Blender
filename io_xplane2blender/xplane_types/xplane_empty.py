import math

import bpy
import mathutils
from io_xplane2blender import xplane_config
from io_xplane2blender import xplane_helpers
from io_xplane2blender.xplane_constants import *
from io_xplane2blender.xplane_types import XPlaneObject


class XPlaneEmpty(XPlaneObject):
    def __init__(self,blenderObject):
        assert blenderObject.type == 'EMPTY'
        super().__init__(blenderObject)
        self.type = 'EMPTY'

    def collect(self):
        pass

    def write(self):
        debug = xplane_config.getDebug()
        indent = self.xplaneBone.getIndent()
        o = super().write()

        special_empty_props = self.blenderObject.xplane.special_empty_props

        if (int(bpy.context.scene.xplane.version) >= 1130 and
                (special_empty_props.special_type == EMPTY_USAGE_EMITTER_PARTICLE or
                 special_empty_props.special_type == EMPTY_USAGE_EMITTER_SOUND)):
            bake_matrix = self.xplaneBone.getBakeMatrixForAttached()
            em_location = xplane_helpers.vec_b_to_x(bake_matrix.to_translation())
            #yaw,pitch,roll
            theta,psi,phi = bake_matrix.to_euler()[:]

            floatToStr = xplane_helpers.floatToStr
            o += '{indent}EMITTER {name} {x} {y} {z} {phi} {theta} {psi}'.format(
                indent=indent,
                name=special_empty_props.emitter_props.name,
                x=floatToStr(em_location.x),
                y=floatToStr(em_location.y),
                z=floatToStr(em_location.z),
                phi=floatToStr(-phi), #yaw right
                theta=floatToStr(theta), #pitch up
                psi=floatToStr(psi)) #roll right

            if special_empty_props.emitter_props.index > 0:
                o += ' {}'.format(special_empty_props.emitter_props.index)
            print(o)

            o +='\n'

        return o


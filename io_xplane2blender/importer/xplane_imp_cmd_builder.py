"""
Statefully builds OBJ commands, including animations and materials.

Takes in OBJ directives and their parameters and outputs at the end Blender datablocks
"""
import collections
import itertools
import math
import pathlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from pprint import pprint
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import bmesh
import bpy
from mathutils import Euler, Vector

from io_xplane2blender.tests import test_creation_helpers
from io_xplane2blender.tests.test_creation_helpers import DatablockInfo, ParentInfo
from io_xplane2blender.xplane_constants import (
    ANIM_TYPE_HIDE,
    ANIM_TYPE_SHOW,
    ANIM_TYPE_TRANSFORM,
)
from io_xplane2blender.xplane_helpers import (
    ExportableRoot,
    floatToStr,
    logger,
    vec_b_to_x,
    vec_x_to_b,
)


@dataclass
class IntermediateDataref:
    """
    Matches xplane_props.XPlaneDataref.

    Made since dataclasses are more flexible then bpy.types.PropertyGroups.
    """

    anim_type: str = ANIM_TYPE_TRANSFORM
    loop: float = 0.0
    path: str = ""
    show_hide_v1: float = 0
    show_hide_v2: float = 0
    values: List[float] = field(default_factory=list)


@dataclass
class IntermediateAnimation:
    """
    An animation is everything generated by one pair of ANIM_trans/rotate pair (or
    the static version). An IntermediateDatablock may have 0 or more of these.
    """

    locations: List[Vector] = field(default_factory=list)
    # A dictionary of rotations along each X,Y,Z axis, where key is "X", "Y", or "Z"
    rotations: Dict[Vector, List[float]] = field(
        default_factory=lambda: collections.defaultdict(list)
    )
    xp_dataref: IntermediateDataref = field(
        default_factory=lambda: IntermediateDataref()
    )

    def apply_animation(self, bl_object: bpy.types.Object):
        def recompose_rotation(value_idx: int):
            tot_rot = Vector((0, 0, 0))
            for axis, degrees in self.rotations.items():
                tot_rot += axis * degrees[value_idx]
            return tot_rot

        current_frame = 1
        if self.xp_dataref.anim_type == ANIM_TYPE_TRANSFORM:
            keyframe_infos = []
            for value_idx, value in enumerate(self.xp_dataref.values):
                keyframe_infos.append(
                    test_creation_helpers.KeyframeInfo(
                        idx=current_frame,
                        dataref_path=self.xp_dataref.path,
                        dataref_value=value,
                        dataref_anim_type=self.xp_dataref.anim_type,
                        location=self.locations[value_idx] if self.locations else None,
                        rotation=recompose_rotation(value_idx)
                        if self.rotations
                        else None,
                    )
                )
                current_frame += 1
        else:
            keyframe_infos = [
                test_creation_helpers.KeyframeInfo(
                    idx=1,
                    dataref_path=self.xp_dataref.path,
                    dataref_show_hide_v1=self.xp_dataref.show_hide_v1,
                    dataref_show_hide_v2=self.xp_dataref.show_hide_v2,
                    dataref_anim_type=self.xp_dataref.anim_type,
                )
            ]

        test_creation_helpers.set_animation_data(bl_object, keyframe_infos)
        current_frame = 1


@dataclass
class IntermediateDatablock:
    datablock_info: DatablockInfo
    # If Datablock is a MESH, these will correspond to (hopefully valid) entries in the idx table and _VT table
    start_idx: Optional[int]
    count: Optional[int]
    # At the start of each IntermediateDatablock's life, this is 0 or 1.
    # During finalization of the tree, they are combined.
    animations_to_apply: List[IntermediateAnimation]

    def build_mesh(self, vt_table: "VTTable") -> bpy.types.Mesh:
        mesh_idxes = vt_table.idxes[self.start_idx : self.start_idx + self.count]
        idx_mapping: Dict[int, int] = {}
        vertices: List[VT] = []

        for mesh_idx in mesh_idxes:
            if mesh_idx not in idx_mapping:
                idx_mapping[mesh_idx] = len(idx_mapping)
                vertices.append(vt_table.vertices[mesh_idx])

        # Thanks senderle, https://stackoverflow.com/a/22045226
        def chunk(it, size):
            it = iter(it)
            return iter(lambda: tuple(itertools.islice(it, size)), ())

        faces: List[Tuple[int, int, int]] = [
            # We reverse the winding order to reverse the faces
            [idx_mapping[idx] for idx in face][::-1]
            for i, face in enumerate(chunk(mesh_idxes, 3))
        ]

        ob = test_creation_helpers.create_datablock_mesh(
            self.datablock_info,
            mesh_src=test_creation_helpers.From_PyData(
                [(v.x, v.y, v.z) for v in vertices], [], faces
            ),
        )
        me = ob.data
        me.update(calc_edges=True)
        uv_layer = me.uv_layers.new()

        if not me.validate(verbose=True):
            for idx in set(itertools.chain.from_iterable(faces)):
                me.vertices[idx].normal = (
                    vertices[idx].nx,
                    vertices[idx].ny,
                    vertices[idx].nz,
                )
                uv_layer.data[idx].uv = vertices[idx].s, vertices[idx].t
        else:
            logger.error("Mesh was not valid, check console for more")

        test_creation_helpers.set_material(ob, "Material")
        return ob


@dataclass
class VT:
    """Where xyz, nxyz are in Blender coords"""

    x: float
    y: float
    z: float
    nx: float
    ny: float
    nz: float
    s: float
    t: float

    def __post_init__(self):
        for attr, factory in type(self).__annotations__.items():
            try:
                setattr(self, attr, factory(getattr(self, attr)))
            except ValueError:
                print(
                    f"Couldn't convert '{attr}''s value ({getattr(self, attr)}) with {factory}"
                )

    def __str__(self) -> str:
        def fmt(s):
            try:
                return floatToStr(float(s))
            except (TypeError, ValueError):
                return s

        return "\t".join(
            fmt(value)
            for attr, value in vars(self).items()
            if not attr.startswith("__")
        )


@dataclass
class VTTable:
    vertices: List[VT] = field(default_factory=list)
    idxes: List[int] = field(default_factory=list)


@dataclass
class _AnimIntermediateStackEntry:
    animation: IntermediateAnimation
    intermediate_datablock: Optional[IntermediateDatablock]


class ImpCommandBuilder:
    def __init__(self, filepath: Path):
        self.root_collection = test_creation_helpers.create_datablock_collection(
            pathlib.Path(filepath).stem
        )

        self.root_collection.xplane.is_exportable_collection = True
        self.vt_table = VTTable([], [])

        # Although we don't end up making this, it is useful for tree problems
        self.root_intermediate_datablock = IntermediateDatablock(
            datablock_info=DatablockInfo(
                datablock_type="EMPTY",
                name="INTER_ROOT",
                collection=self.root_collection,
            ),
            start_idx=None,
            count=None,
            animations_to_apply=[],
        )

        # --- Animation Builder States ----------------------------------------
        # Instead of build at seperate parent/child relationship in Datablock info, we just save everything we make here
        self._blocks: List[IntermediateDatablock] = [self.root_intermediate_datablock]
        self._last_axis: Optional[Vector] = None
        self._anim_intermediate_stack = collections.deque()
        self._anim_count: Sequence[int] = collections.deque()
        # ---------------------------------------------------------------------

    def build_cmd(
        self, directive: str, *args: List[Union[float, int, str]], name_hint: str = ""
    ):
        """
        Given the directive and it's arguments, correctly handle each case.

        args must be every arg, in order, correctly typed, needed to build the command
        """

        def begin_new_frame() -> None:
            if not self._top_intermediate_datablock:
                parent = self.root_intermediate_datablock
            else:
                parent = self._top_intermediate_datablock

            empt = IntermediateDatablock(
                datablock_info=DatablockInfo(
                    "EMPTY",
                    self._next_empty_name(),
                    ParentInfo(parent.datablock_info.name),
                    self.root_collection,
                ),
                start_idx=None,
                count=None,
                animations_to_apply=[],
            )
            self._blocks.append(empt)

            self._anim_intermediate_stack.append(
                _AnimIntermediateStackEntry(IntermediateAnimation(), empt)
            )
            self._anim_count[-1] += 1
            empt.animations_to_apply.append(self._top_animation)

        if directive == "VT":
            self.vt_table.vertices.append(VT(*args))
        elif directive == "IDX":
            self.vt_table.idxes.append(args[0])
        elif directive == "IDX10":
            # idx error etc
            self.vt_table.idxes.extend(args)
        elif directive == "TRIS":
            start_idx = args[0]
            count = args[1]
            if not self._anim_intermediate_stack:
                parent: IntermediateDatablock = self.root_intermediate_datablock
            else:
                parent: IntermediateDatablock = self._anim_intermediate_stack[
                    -1
                ].intermediate_datablock

            intermediate_datablock = IntermediateDatablock(
                datablock_info=DatablockInfo(
                    datablock_type="MESH",
                    name=name_hint or self._next_object_name(),
                    # How do we keep track of this
                    parent_info=ParentInfo(parent.datablock_info.name),
                    collection=self.root_collection,
                ),
                start_idx=start_idx,
                count=count,
                animations_to_apply=[],
            )
            self._blocks.append(intermediate_datablock)

        elif directive == "ANIM_begin":
            self._anim_count.append(0)
        elif directive == "ANIM_end":
            for i in range(self._anim_count.pop()):
                self._anim_intermediate_stack.pop()
        elif directive == "ANIM_trans_begin":
            dataref_path = args[0]

            begin_new_frame()
            self._top_animation.xp_dataref = IntermediateDataref(
                anim_type=ANIM_TYPE_TRANSFORM,
                loop=0,
                path=dataref_path,
                show_hide_v1=0,
                show_hide_v2=0,
                values=[],
            )
        elif directive == "ANIM_trans_key":
            value = args[0]
            location = args[1]
            self._top_animation.locations.append(location)
            self._top_dataref.values.append(value)
        elif directive == "ANIM_trans_end":
            pass
        elif directive in {"ANIM_hide", "ANIM_show"}:
            v1, v2 = args[:2]
            dataref_path = args[2]
            begin_new_frame()
            self._top_dataref.anim_type = directive.replace("ANIM_", "")
            self._top_dataref.path = dataref_path
            self._top_dataref.show_hide_v1 = v1
            self._top_dataref.show_hide_v2 = v2
        elif directive == "ANIM_rotate_begin":
            axis = args[0]
            dataref_path = args[1]
            self._last_axis = Vector(map(abs, axis))
            begin_new_frame()
            self._top_animation.xp_dataref = IntermediateDataref(
                anim_type=ANIM_TYPE_TRANSFORM,
                loop=0,
                path=dataref_path,
                show_hide_v1=0,
                show_hide_v2=0,
                values=[],
            )
        elif directive == "ANIM_rotate_key":
            value = args[0]
            degrees = args[1]
            self._top_animation.rotations[self._last_axis.freeze()].append(degrees)
            self._top_dataref.values.append(value)
        elif directive == "ANIM_rotate_end":
            self._last_axis = None
        elif directive == "ANIM_keyframe_loop":
            loop = args[0]
            self._top_dataref.loop = loop
        elif directive == "ANIM_trans":
            xyz1 = args[0]
            xyz2 = args[1]
            v1, v2 = args[2:4]
            path = args[4]
            begin_new_frame()
            self._top_animation.locations.append(xyz1)
            self._top_animation.locations.append(xyz2)
            self._top_dataref.values.extend((v1, v2))
            self._top_dataref.path = path

        elif directive == "ANIM_rotate":
            dxyz = args[0]
            r1, r2 = args[1:3]
            v1, v2 = args[3:5]
            path = args[5]

            begin_new_frame()
            self._top_animation.rotations[dxyz.freeze()].append(r1)
            self._top_animation.rotations[dxyz.freeze()].append(r2)
            self._top_dataref.values.extend((v1, v2))
            self._top_dataref.path = path

        else:
            assert False, f"{directive} is not supported yet"

    def finalize_intermediate_blocks(self) -> Set[str]:
        """The last step after parsing, converting
        data to intermediate structures, clean up and error checking.

        Returns a set with FINISHED or CANCELLED, matching the returns of bpy
        operators
        """
        # Since we're using root collections mode, our INTER_ROOT empty datablock isn't made
        # and we pretend its a collection.
        for intermediate_block in self._blocks[1:]:
            db_info = intermediate_block.datablock_info
            if db_info.parent_info.parent == "INTER_ROOT":
                db_info.parent_info = None
            if db_info.datablock_type == "EMPTY":
                ob = test_creation_helpers.create_datablock_empty(db_info)
            elif db_info.datablock_type == "MESH":
                ob = intermediate_block.build_mesh(self.vt_table)
            for animation in intermediate_block.animations_to_apply:
                animation.apply_animation(ob)
        bpy.context.scene.frame_current = 1
        return {"FINISHED"}

    @property
    def _top_animation(self) -> Optional[IntermediateAnimation]:
        try:
            return self._anim_intermediate_stack[-1].animation
        except IndexError:
            return None

    @_top_animation.setter
    def _top_animation(self, value: IntermediateAnimation) -> None:
        self._anim_intermediate_stack[-1].animation = value

    @property
    def _top_intermediate_datablock(self) -> Optional[IntermediateDatablock]:
        try:
            return self._anim_intermediate_stack[-1].intermediate_datablock
        except IndexError:
            return None

    @_top_intermediate_datablock.setter
    def _top_intermediate_datablock(self, value: IntermediateDatablock) -> None:
        self._anim_intermediate_stack[-1].intermediate_datablock = value

    @property
    def _top_dataref(self) -> Optional[IntermediateDataref]:
        return self._top_animation.xp_dataref

    @_top_dataref.setter
    def _top_dataref(self, value: IntermediateDataref) -> None:
        self._top_animation.xp_dataref = value

    def _next_empty_name(self) -> str:
        return f"ImpEmpty.{sum(1 for block in self._blocks if block.datablock_info.datablock_type == 'EMPTY'):03}_{hex(hash(self.root_collection.name))[2:6]}"

    def _next_object_name(self) -> str:
        return f"ImpMesh.{sum(1 for block in self._blocks if block.datablock_info.datablock_type == 'MESH'):03}_{hex(hash(self.root_collection.name))[2:6]}"

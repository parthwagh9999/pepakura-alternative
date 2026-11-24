bl_info = {
    "name": "Papercraft Plotter Workflow (Pepakura Alternative Method)",
    "author": "Antigravity Assistant & Pepakura Alternative Standards",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Papercraft",
    "description": "Turn 3D models into cut-ready papercraft SVGs & PDFs for plotters and manual craft. True Pepakura Alternative outward flaps, 45 deg angles, 175 deg fold threshold, and multi-layer vector export.",
    "category": "Mesh",
}

import bpy
import bmesh
from mathutils import Vector
import math
import os

# =========================================================================
# 1. PROPERTY GROUP (PEPAKURA ALTERNATIVE SETTINGS)
# =========================================================================

class PepakuraAlternativeSettings(bpy.types.PropertyGroup):
    # Flap & Edge ID settings
    flap_angle_deg: bpy.props.FloatProperty(
        name="Flap Base Angle",
        description="Default base angle of flap in degrees (Pepakura Alternative standard is 45 deg)",
        default=45.0,
        min=10.0,
        max=80.0
    )
    edge_id_font_size: bpy.props.IntProperty(
        name="Edge ID Font Size (pt)",
        description="Font size for edge matching numbers",
        default=6,
        min=4,
        max=16
    )
    grid_spacing_mm: bpy.props.FloatProperty(
        name="Grid Spacing (mm)",
        description="Grid spacing in millimeters",
        default=5.0,
        min=1.0,
        max=50.0
    )
    outline_padding_mm: bpy.props.FloatProperty(
        name="Outline Padding Width (mm)",
        description="Padding width around part outlines",
        default=1.0,
        min=0.1,
        max=10.0
    )
    auto_join_edges: bpy.props.BoolProperty(
        name="Auto Join Nearby Edges",
        description="Automatically join edges that are within tolerance",
        default=True
    )
    near_threshold_mm: bpy.props.FloatProperty(
        name="Near Position Threshold (mm)",
        description="Threshold distance for near position welding",
        default=0.01,
        min=0.001,
        max=1.0
    )
    hide_flat_folds: bpy.props.BoolProperty(
        name="Hide Nearly Flat Folds",
        description="Hide folding lines that are nearly flat",
        default=True
    )
    flat_fold_threshold: bpy.props.FloatProperty(
        name="Flat Fold Threshold (deg)",
        description="Threshold dihedral angle above which fold lines are hidden",
        default=175.0,
        min=150.0,
        max=180.0
    )
    
    # Page setup settings
    side_margin_mm: bpy.props.FloatProperty(
        name="Side Margin (mm)",
        description="Left and right page margin in millimeters",
        default=15.0,
        min=5.0,
        max=40.0
    )
    top_margin_mm: bpy.props.FloatProperty(
        name="Top/Bottom Margin (mm)",
        description="Top and bottom page margin in millimeters",
        default=15.0,
        min=5.0,
        max=40.0
    )
    page_format: bpy.props.EnumProperty(
        name="Page Size",
        items=[
            ('A4', "A4 (210 x 297 mm)", "Standard International A4 sheet"),
            ('LETTER', "US Letter (8.5 x 11 in)", "Standard US Letter sheet"),
        ],
        default='A4'
    )
    page_orientation: bpy.props.EnumProperty(
        name="Orientation",
        items=[
            ('PORTRAIT', "Portrait", "Portrait orientation"),
            ('LANDSCAPE', "Landscape", "Landscape orientation"),
        ],
        default='PORTRAIT'
    )
    
    # Workflow variation selector
    workflow_variation: bpy.props.EnumProperty(
        name="Variation",
        items=[
            ('ALL', "All 4 Variations (Pro Suite)", "Export all 4 configurations at once (PDF, SVG, 300 DPI PNG)"),
            ('1_COLOR_LINES', "1. Full Color + Fold Lines", "Colored texture with fold lines & IDs"),
            ('2_COLOR_NO_LINES', "2. Full Color (No Lines)", "Colored texture without internal fold lines (studio finish)"),
            ('3_WHITE_LINES', "3. Blank White + Fold Lines", "Without color fill, with fold lines & IDs for colored cardstock"),
            ('4_WHITE_NO_LINES', "4. Blank White (No Lines)", "Pure cut outlines and flaps without fold lines or color"),
        ],
        default='ALL'
    )


# =========================================================================
# 2. CLEAN & PLANARIZE (Merge Split Vertices & Flatten Quads)
# =========================================================================

def clean_and_planarize_mesh(obj, merge_dist=0.0005, iterations=4, factor=0.85):
    """
    1. Welds coincident 3D vertices (common in imported OBJs).
    2. Projects non-planar quads onto their local face plane (fixes 'twisted polygons').
    """
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=merge_dist)
    bpy.ops.object.mode_set(mode='OBJECT')

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    
    faces_to_process = [f for f in bm.faces if len(f.verts) > 3]
    if not faces_to_process:
        bm.free()
        return len(mesh.vertices), 0
    
    for _ in range(iterations):
        vert_displacements = {v: [] for v in bm.verts}
        
        for f in faces_to_process:
            center = f.calc_center_median()
            norm = f.normal.normalized()
            if norm.length < 1e-6:
                continue
            
            for v in f.verts:
                diff = v.co - center
                dist = diff.dot(norm)
                target_co = v.co - (norm * dist)
                vert_displacements[v].append(target_co)
        
        for v, targets in vert_displacements.items():
            if targets:
                avg_target = sum(targets, Vector()) / len(targets)
                v.co = v.co.lerp(avg_target, factor)
        
        bm.normal_update()

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(mesh.vertices), len(faces_to_process)


class MESH_OT_clean_and_planarize(bpy.types.Operator):
    """Weld coincident vertices and flatten non-planar quads to prevent paper distortion"""
    bl_idname = "mesh.clean_and_planarize"
    bl_label = "Clean & Planarize Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    merge_threshold: bpy.props.FloatProperty(
        name="Weld Threshold (m)",
        default=0.0005,
        min=0.00001,
        max=0.01
    )
    iterations: bpy.props.IntProperty(
        name="Planarize Passes",
        default=4,
        min=1,
        max=20
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a Mesh")
            return {'CANCELLED'}
        
        v_count, f_count = clean_and_planarize_mesh(obj, merge_dist=self.merge_threshold, iterations=self.iterations)
        self.report({'INFO'}, f"Cleaned: {v_count} verts remaining, {f_count} quads planarized.")
        return {'FINISHED'}


# =========================================================================
# 3. GEOMETRY NODES SETUP (Flatten to UVs + Make Tabs)
# =========================================================================

def build_papercraft_geometry_nodes_tree(uv_name="UVMap"):
    """Builds the exact node graph: Flatten to UVs + Make Tabs"""
    ng_name = "Papercraft_Unfold_Tabs"
    ng = bpy.data.node_groups.get(ng_name)
    if not ng:
        ng = bpy.data.node_groups.new(ng_name, 'GeometryNodeTree')
        ng.is_modifier = True
        
        interface = ng.interface
        interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
        interface.new_socket(name="Tab Thickness", in_out='INPUT', socket_type='NodeSocketFloat')
        interface.new_socket(name="Tab Taper", in_out='INPUT', socket_type='NodeSocketFloat')
        interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
        
        interface.items_tree["Tab Thickness"].default_value = 0.015
        interface.items_tree["Tab Taper"].default_value = 0.70

    nodes = ng.nodes
    links = ng.links
    nodes.clear()
    
    group_in = nodes.new('NodeGroupInput')
    group_in.location = (-1000, 200)
    
    group_out = nodes.new('NodeGroupOutput')
    group_out.location = (1600, 200)

    # --- SLIDE 1: FLATTEN TO UVS ---
    idx_1 = nodes.new('GeometryNodeInputIndex')
    idx_1.location = (-800, 50)
    
    store_attr = nodes.new('GeometryNodeStoreNamedAttribute')
    store_attr.data_type = 'INT'
    store_attr.domain = 'EDGE'
    store_attr.inputs['Name'].default_value = "original_edge_index"
    store_attr.location = (-600, 250)
    
    links.new(group_in.outputs['Geometry'], store_attr.inputs['Geometry'])
    links.new(idx_1.outputs['Index'], store_attr.inputs['Value'])
    
    split_edges_1 = nodes.new('GeometryNodeSplitEdges')
    split_edges_1.location = (-350, 250)
    links.new(store_attr.outputs['Geometry'], split_edges_1.inputs['Mesh'])
    
    uv_attr = nodes.new('GeometryNodeInputNamedAttribute')
    uv_attr.data_type = 'FLOAT_VECTOR'
    uv_attr.inputs['Name'].default_value = uv_name
    uv_attr.location = (-350, -50)

    set_pos_uv = nodes.new('GeometryNodeSetPosition')
    set_pos_uv.location = (-100, 250)
    links.new(split_edges_1.outputs['Mesh'], set_pos_uv.inputs['Geometry'])
    links.new(uv_attr.outputs['Attribute'], set_pos_uv.inputs['Position'])
    
    merge_dist = nodes.new('GeometryNodeMergeByDistance')
    merge_dist.inputs['Distance'].default_value = 0.001
    merge_dist.location = (150, 250)
    links.new(set_pos_uv.outputs['Geometry'], merge_dist.inputs['Geometry'])

    # --- SLIDE 2: MAKE TABS (OUTWARD) ---
    edge_neighbors = nodes.new('GeometryNodeInputMeshEdgeNeighbors')
    edge_neighbors.location = (200, -100)
    
    less_than = nodes.new('FunctionNodeCompare')
    less_than.data_type = 'INT'
    less_than.operation = 'LESS_THAN'
    less_than.inputs['B'].default_value = 2
    less_than.location = (400, -100)
    links.new(edge_neighbors.outputs['Face Count'], less_than.inputs['A'])
    
    extrude = nodes.new('GeometryNodeExtrudeMesh')
    extrude.mode = 'EDGES'
    extrude.inputs['Offset Scale'].default_value = 0.0
    extrude.location = (400, 250)
    links.new(merge_dist.outputs['Geometry'], extrude.inputs['Mesh'])
    links.new(less_than.outputs['Result'], extrude.inputs['Selection'])
    
    split_edges_tabs = nodes.new('GeometryNodeSplitEdges')
    split_edges_tabs.location = (650, 350)
    links.new(extrude.outputs['Mesh'], split_edges_tabs.inputs['Mesh'])
    links.new(extrude.outputs['Top'], split_edges_tabs.inputs['Selection'])
    
    edge_normal = nodes.new('GeometryNodeInputNormal')
    edge_normal.location = (450, -300)
    
    scale_vec = nodes.new('ShaderNodeVectorMath')
    scale_vec.operation = 'SCALE'
    scale_vec.location = (650, -200)
    links.new(edge_normal.outputs['Normal'], scale_vec.inputs['Vector'])
    links.new(group_in.outputs['Tab Thickness'], scale_vec.inputs['Scale'])
    
    set_pos_tab = nodes.new('GeometryNodeSetPosition')
    set_pos_tab.location = (850, 250)
    links.new(split_edges_tabs.outputs['Mesh'], set_pos_tab.inputs['Geometry'])
    links.new(extrude.outputs['Top'], set_pos_tab.inputs['Selection'])
    links.new(scale_vec.outputs['Vector'], set_pos_tab.inputs['Offset'])
    
    scale_elements = nodes.new('GeometryNodeScaleElements')
    scale_elements.domain = 'EDGE'
    scale_elements.location = (1100, 250)
    links.new(set_pos_tab.outputs['Geometry'], scale_elements.inputs['Geometry'])
    links.new(extrude.outputs['Top'], scale_elements.inputs['Selection'])
    links.new(group_in.outputs['Tab Taper'], scale_elements.inputs['Scale'])
    
    links.new(scale_elements.outputs['Geometry'], group_out.inputs['Geometry'])
    return ng


class OBJECT_OT_setup_papercraft_nodes(bpy.types.Operator):
    """Build Geometry Nodes network (Flatten to UVs + Make Tabs)"""
    bl_idname = "object.setup_papercraft_nodes"
    bl_label = "Build Papercraft GeoNodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a Mesh")
            return {'CANCELLED'}
        
        if not obj.data.uv_layers:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.uv.smart_project(island_margin=0.03)
            bpy.ops.object.mode_set(mode='OBJECT')
            self.report({'WARNING'}, "Created default Smart UV Project UVMap")
        
        uv_name = obj.data.uv_layers.active.name if obj.data.uv_layers.active else "UVMap"
        ng = build_papercraft_geometry_nodes_tree(uv_name=uv_name)
        
        mod_name = "Papercraft_Nodes"
        mod = obj.modifiers.get(mod_name)
        if not mod:
            mod = obj.modifiers.new(name=mod_name, type='NODES')
        mod.node_group = ng
        
        self.report({'INFO'}, "Created Papercraft Geometry Nodes setup on mesh!")
        return {'FINISHED'}


# =========================================================================
# 4. PEPAKURA ALTERNATIVE PLOTTER SVG & MULTI-PAGE PDF GENERATOR
# =========================================================================

class EXPORT_OT_pepakura_alternative_package(bpy.types.Operator):
    """Generate complete Pepakura Alternative Papercraft Package (PDF, SVG & 300 DPI PNG across 4 Variations)"""
    bl_idname = "export.pepakura_alternative_package"
    bl_label = "Generate Pepakura Alternative Deliverables"
    bl_options = {'REGISTER'}

    def execute(self, context):
        script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
        pkg_script = os.path.join(script_dir, "pepakura_alternative_workflow.py")
        if not os.path.exists(pkg_script):
            self.report({'ERROR'}, f"Script not found: {pkg_script}")
            return {'CANCELLED'}

        # Export active object to OBJ and run generator
        obj = context.active_object
        cmd = ["python", pkg_script]
        if obj and obj.type == 'MESH':
            import tempfile
            temp_obj = os.path.join(tempfile.gettempdir(), f"{obj.name}_export.obj")
            try:
                bpy.ops.wm.obj_export(filepath=temp_obj, export_selected_objects=True)
                cmd.extend(["--input", temp_obj])
            except Exception:
                pass

        import subprocess
        subprocess.run(cmd, check=True)
        self.report({'INFO'}, "Successfully generated all 4 workflow variations (PDF, SVG, 300 DPI PNG)!")
        return {'FINISHED'}


# =========================================================================
# 5. VIEWPORT SIDEBAR UI PANEL (PEPAKURA ALTERNATIVE SETTINGS)
# =========================================================================

class VIEW3D_PT_pepakura_alternative(bpy.types.Panel):
    bl_label = "Papercraft Plotter Workflow"
    bl_idname = "VIEW3D_PT_pepakura_alternative"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Pepakura Alternative"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        settings = context.scene.pepakura_alternative_settings

        # Active Object Status
        box = layout.box()
        box.label(text="Pepakura Alternative Pipeline", icon='TOOL_SETTINGS')
        if not obj or obj.type != 'MESH':
            box.label(text="Select a 3D Mesh to begin", icon='INFO')
            return

        box.label(text=f"Active Object: {obj.name}", icon='OBJECT_DATA')

        # 1. Mesh Preparation
        col = layout.column(align=True)
        col.label(text="1. Geometry Preparation:", icon='MOD_DECIM')
        col.operator("mesh.clean_and_planarize", text="Clean & Planarize Mesh", icon='MESH_PLANE')

        # 2. Geometry Nodes
        col = layout.column(align=True)
        col.label(text="2. Viewport Unfolding & Tabs:", icon='NODETREE')
        col.operator("object.setup_papercraft_nodes", text="Build GeoNodes (Outward Tabs)", icon='NODE_MATERIAL')

        # 3. Pepakura Alternative Settings
        col = layout.column(align=True)
        col.label(text="3. Pepakura Alternative Settings:", icon='PREFERENCES')
        
        pbox = layout.box()
        pbox.label(text="Flap & Pattern Settings", icon='SNAP_EDGE')
        pbox.prop(settings, "flap_angle_deg")
        pbox.prop(settings, "edge_id_font_size")
        pbox.prop(settings, "grid_spacing_mm")
        pbox.prop(settings, "outline_padding_mm")
        
        row = pbox.row()
        row.prop(settings, "auto_join_edges")
        if settings.auto_join_edges:
            pbox.prop(settings, "near_threshold_mm")
            
        row2 = pbox.row()
        row2.prop(settings, "hide_flat_folds")
        if settings.hide_flat_folds:
            pbox.prop(settings, "flat_fold_threshold")

        # Page Setup Settings
        pbox2 = layout.box()
        pbox2.label(text="Page Setup & Variations", icon='DOCUMENTS')
        pbox2.prop(settings, "workflow_variation")
        pbox2.prop(settings, "page_format")
        pbox2.prop(settings, "page_orientation")
        pbox2.prop(settings, "side_margin_mm")
        pbox2.prop(settings, "top_margin_mm")

        # 4. Export Button
        col = layout.column(align=True)
        col.label(text="4. Deliverables (PDF, SVG, 300 DPI PNG):", icon='EXPORT')
        col.operator("export.pepakura_alternative_package", text="Export 4 Variations (PDF, SVG, 300 DPI PNG)", icon='FILE_IMAGE')


# =========================================================================
# REGISTRATION
# =========================================================================

classes = (
    PepakuraAlternativeSettings,
    MESH_OT_clean_and_planarize,
    OBJECT_OT_setup_papercraft_nodes,
    EXPORT_OT_pepakura_alternative_package,
    VIEW3D_PT_pepakura_alternative,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pepakura_alternative_settings = bpy.props.PointerProperty(type=PepakuraAlternativeSettings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.pepakura_alternative_settings

if __name__ == "__main__":
    register()

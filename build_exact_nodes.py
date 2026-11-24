import bpy

def build_papercraft_geometry_nodes():
    # -------------------------------------------------------------
    # 1. MAIN TREE: FLATTEN TO UVS + MAKE TABS (Slides 1 & 2)
    # -------------------------------------------------------------
    ng_name = "Papercraft_Unfold_Tabs"
    if ng_name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[ng_name])
    
    ng = bpy.data.node_groups.new(ng_name, 'GeometryNodeTree')
    ng.is_modifier = True
    
    interface = ng.interface
    interface.new_socket(name="Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    interface.new_socket(name="UVMap", in_out='INPUT', socket_type='NodeSocketVector')
    interface.new_socket(name="Tab Thickness", in_out='INPUT', socket_type='NodeSocketFloat')
    interface.new_socket(name="Tab Taper", in_out='INPUT', socket_type='NodeSocketFloat')
    interface.new_socket(name="Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    
    ng.interface.items_tree["Tab Thickness"].default_value = 0.015
    ng.interface.items_tree["Tab Taper"].default_value = 0.70

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
    
    set_pos_uv = nodes.new('GeometryNodeSetPosition')
    set_pos_uv.location = (-100, 250)
    links.new(split_edges_1.outputs['Mesh'], set_pos_uv.inputs['Geometry'])
    links.new(group_in.outputs['UVMap'], set_pos_uv.inputs['Position'])
    
    merge_dist = nodes.new('GeometryNodeMergeByDistance')
    merge_dist.inputs['Distance'].default_value = 0.001
    merge_dist.location = (150, 250)
    links.new(set_pos_uv.outputs['Geometry'], merge_dist.inputs['Geometry'])

    # --- SLIDE 2: MAKE TABS ---
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

    # -------------------------------------------------------------
    # 2. SLIDE 3: CONFIGURABLE CUT AND FOLD LINES (Dashed Folds)
    # -------------------------------------------------------------
    ng3_name = "Papercraft_Dashed_Fold_Lines"
    if ng3_name in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups[ng3_name])
        
    ng3 = bpy.data.node_groups.new(ng3_name, 'GeometryNodeTree')
    ng3.is_modifier = True
    
    ng3.interface.new_socket(name="Curve", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng3.interface.new_socket(name="Dashed Line Spacing", in_out='INPUT', socket_type='NodeSocketFloat')
    ng3.interface.new_socket(name="Mesh", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng3.interface.items_tree["Dashed Line Spacing"].default_value = 1.0

    n3 = ng3.nodes
    l3 = ng3.links
    n3.clear()
    
    gin3 = n3.new('NodeGroupInput')
    gin3.location = (-600, 0)
    gout3 = n3.new('NodeGroupOutput')
    gout3.location = (800, 0)
    
    mul = n3.new('ShaderNodeMath')
    mul.operation = 'MULTIPLY'
    mul.inputs[0].default_value = 0.010
    mul.location = (-400, -150)
    l3.new(gin3.outputs['Dashed Line Spacing'], mul.inputs[1])
    
    resample = n3.new('GeometryNodeResampleCurve')
    resample.inputs['Mode'].default_value = 'Length'
    resample.location = (-200, 50)
    l3.new(gin3.outputs['Curve'], resample.inputs['Curve'])
    l3.new(mul.outputs['Value'], resample.inputs['Length'])
    
    c2m = n3.new('GeometryNodeCurveToMesh')
    c2m.location = (50, 50)
    l3.new(resample.outputs['Curve'], c2m.inputs['Curve'])
    
    idx3 = n3.new('GeometryNodeInputIndex')
    idx3.location = (-100, -250)
    
    mod = n3.new('ShaderNodeMath')
    mod.operation = 'MODULO'
    mod.inputs[1].default_value = 2.0
    mod.location = (100, -250)
    l3.new(idx3.outputs['Index'], mod.inputs[0])
    
    eq = n3.new('FunctionNodeCompare')
    eq.data_type = 'INT'
    eq.operation = 'EQUAL'
    eq.inputs['B'].default_value = 0
    eq.location = (300, -250)
    l3.new(mod.outputs['Value'], eq.inputs['A'])
    
    del_geom = n3.new('GeometryNodeDeleteGeometry')
    del_geom.domain = 'EDGE'
    del_geom.mode = 'ALL'
    del_geom.location = (450, 50)
    l3.new(c2m.outputs['Mesh'], del_geom.inputs['Geometry'])
    l3.new(eq.outputs['Result'], del_geom.inputs['Selection'])
    
    l3.new(del_geom.outputs['Geometry'], gout3.inputs['Mesh'])

    print("SUCCESS: Papercraft Geometry Node trees generated without errors!")

build_papercraft_geometry_nodes()

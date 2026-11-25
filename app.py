#!/usr/bin/env python3
"""
=============================================================================
Pepakura Alternative: Interactive Web Studio
=============================================================================
A local web application for turning any 3D model (.obj) into precision
papercraft templates for manual crafting and cutting plotters.

Run with:
    python app.py
Open:
    http://localhost:8080
=============================================================================
"""

import os
import sys
import json
import base64
import tempfile
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# Import universal pipeline
from pepakura_alternative_workflow import (
    Model3D, unfold_model_to_parts, pack_parts_onto_pages,
    export_svg_page, export_pdf_pages, render_pdf_to_300dpi_pngs
)

PORT = 8080
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pepakura Alternative — 3D Papercraft Studio</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/OBJLoader.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
  <style>
    .pattern-bg { background-image: radial-gradient(#e2e8f0 1px, transparent 1px); background-size: 16px 16px; }
  </style>
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen flex flex-col font-sans">

  <!-- Header -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 py-3.5 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-9 h-9 bg-indigo-600 rounded-lg flex items-center justify-center text-white font-bold text-lg shadow-sm">
          ✂️
        </div>
        <div>
          <h1 class="text-lg font-bold text-slate-900 leading-tight">Pepakura Alternative</h1>
          <p class="text-xs text-slate-500">Universal 3D Papercraft &amp; Cutting Plotter Pipeline</p>
        </div>
      </div>
      <div class="flex items-center space-x-4 text-xs font-medium text-slate-600">
        <span class="inline-flex items-center px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
          ● Ready (Any 3D Model)
        </span>
        <a href="https://github.com/parthwagh9999/pepakura-alternative" target="_blank" class="hover:text-indigo-600 flex items-center gap-1">
          GitHub Repository ↗
        </a>
      </div>
    </div>
  </header>

  <!-- Main Content -->
  <main class="max-w-7xl mx-auto px-4 py-6 flex-1 w-full grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- Left Controls Panel (5 cols) -->
    <div class="lg:col-span-5 space-y-6">

      <!-- Upload Box -->
      <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-500 mb-3 flex items-center gap-2">
          <span>1. Select 3D Model</span>
        </h2>
        <div id="drop-zone" class="border-2 border-dashed border-slate-300 hover:border-indigo-500 rounded-lg p-6 text-center cursor-pointer transition bg-slate-50/50">
          <input type="file" id="obj-input" accept=".obj" class="hidden">
          <div class="text-3xl mb-2">📦</div>
          <p class="text-sm font-medium text-slate-700" id="file-name-label">Drag &amp; drop any .obj file here, or click to browse</p>
          <p class="text-xs text-slate-400 mt-1">Accepts any 3D polygonal mesh (low-poly or complex)</p>
        </div>

        <div class="mt-3 flex justify-between items-center text-xs">
          <button id="btn-use-demo" class="text-indigo-600 hover:text-indigo-800 font-medium">
            Use built-in polyhedral demo model
          </button>
          <span id="model-status" class="text-slate-400 italic">No file loaded yet</span>
        </div>
      </div>

      <!-- Parameters & Flap Settings -->
      <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-500 flex items-center gap-2">
          <span>2. Papercraft &amp; Flap Settings</span>
        </h2>

        <!-- Flap Size (mm) -->
        <div>
          <div class="flex justify-between items-center mb-1">
            <label for="flap-size" class="text-xs font-semibold text-slate-700">Glue Flap Size / Depth (mm):</label>
            <span id="flap-size-val" class="text-xs font-bold px-2 py-0.5 rounded bg-indigo-50 text-indigo-700">5.5 mm</span>
          </div>
          <div class="flex items-center space-x-3">
            <input type="range" id="flap-size" min="2.0" max="15.0" step="0.5" value="5.5" class="w-full accent-indigo-600">
            <input type="number" id="flap-size-num" min="2.0" max="20.0" step="0.5" value="5.5" class="w-16 px-2 py-1 text-xs border border-slate-300 rounded text-center">
          </div>
          <p class="text-[11px] text-slate-400 mt-1">Outward 45° trapezoidal tabs with cross-hatch grip pattern.</p>
        </div>

        <!-- Paper Format & Margins -->
        <div class="grid grid-cols-2 gap-3 pt-2 border-t border-slate-100">
          <div>
            <label class="text-xs font-semibold text-slate-700 block mb-1">Paper Format:</label>
            <select id="paper-format" class="w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded bg-white font-medium">
              <option value="LETTER" selected>US Letter (8.5 × 11.0 in)</option>
              <option value="A4">A4 (210 × 297 mm)</option>
            </select>
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-700 block mb-1">Page Margin (mm):</label>
            <input type="number" id="page-margin" value="15.0" min="5.0" max="30.0" step="1.0" class="w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded bg-white">
          </div>
        </div>

        <!-- Workflow Variations -->
        <div class="pt-2 border-t border-slate-100">
          <label class="text-xs font-semibold text-slate-700 block mb-1">Template Variation:</label>
          <select id="variation-select" class="w-full px-2.5 py-1.5 text-xs border border-slate-300 rounded bg-white font-medium">
            <option value="1" selected>1. Full Color + Fold Lines + Edge IDs</option>
            <option value="2">2. Full Color (Clean Outlines Only)</option>
            <option value="3">3. Blank White + Fold Lines + Edge IDs</option>
            <option value="4">4. Blank White (Clean Outlines Only)</option>
            <option value="all">All 4 Variations Package</option>
          </select>
        </div>

        <!-- Edge Joining Toggle -->
        <div class="pt-2 border-t border-slate-100 flex items-start space-x-2.5">
          <input type="checkbox" id="join-edges" class="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500">
          <div>
            <label for="join-edges" class="text-xs font-semibold text-slate-700 cursor-pointer">
              Smart Edge Joining (Minimize Cutting &amp; Gluing)
            </label>
            <p class="text-[11px] text-slate-400">Joins faces across fold lines while preventing circular/flap overlaps and respecting page margins.</p>
          </div>
        </div>

        <!-- Topology Scope & Roadmap Tip -->
        <div class="p-2.5 bg-amber-50 border border-amber-200/70 rounded-lg text-[11px] text-amber-800 space-y-0.5">
          <p class="font-semibold flex items-center gap-1">
            <span>💡 Topology &amp; Edge Joining:</span>
          </p>
          <p class="text-amber-700 leading-normal">
            Supports both individual planar unfolding and automated flap-aware edge joining (conical fans, strips, and nested concave islands) with exact 1:1 millimeter scale preservation.
          </p>
        </div>

        <!-- Generate Button -->
        <button id="btn-generate" class="w-full py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg font-semibold text-sm shadow-sm transition flex items-center justify-center space-x-2">
          <span>✨ Generate Papercraft Templates</span>
        </button>
      </div>

      <!-- 3D Model Interactive Viewport -->
      <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div class="flex justify-between items-center mb-2">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500">3D Model Interactive Preview</h3>
          <span class="text-[11px] text-slate-400">Click &amp; drag to rotate</span>
        </div>
        <div id="three-container" class="w-full h-48 bg-slate-900 rounded-lg overflow-hidden relative">
          <div id="three-placeholder" class="absolute inset-0 flex items-center justify-center text-slate-400 text-xs">
            Interactive 3D Viewport
          </div>
        </div>
      </div>

    </div>

    <!-- Right Results Panel (7 cols) -->
    <div class="lg:col-span-7 space-y-6">

      <!-- Output Deliverables Card -->
      <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm min-h-[600px] flex flex-col">
        <div class="flex flex-wrap items-center justify-between pb-4 border-b border-slate-200 gap-2">
          <div>
            <h2 class="text-base font-bold text-slate-900">Generated Templates</h2>
            <p id="output-meta" class="text-xs text-slate-500">Click generate to preview your unfolded sheets</p>
          </div>
          <!-- Download Buttons -->
          <div id="download-actions" class="hidden flex items-center space-x-2">
            <a id="dl-pdf" href="#" download="papercraft_template.pdf" class="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-xs font-semibold rounded shadow-sm flex items-center gap-1">
              📄 Vector PDF
            </a>
            <a id="dl-svg" href="#" download="papercraft_page1.svg" class="px-3 py-1.5 bg-sky-600 hover:bg-sky-700 text-white text-xs font-semibold rounded shadow-sm flex items-center gap-1">
              📐 Plotter SVG
            </a>
            <a id="dl-png" href="#" download="papercraft_sheet_300dpi.png" class="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded shadow-sm flex items-center gap-1">
              🖼️ 300 DPI PNG
            </a>
          </div>
        </div>

        <!-- Page Navigator Tabs -->
        <div id="page-tabs" class="hidden flex items-center space-x-2 py-3 border-b border-slate-100 text-xs">
          <span class="font-semibold text-slate-500 mr-2">Sheets:</span>
          <!-- Dynamic buttons inserted here -->
        </div>

        <!-- SVG Preview Container -->
        <div id="preview-area" class="flex-1 flex items-center justify-center p-4 bg-slate-100 rounded-lg pattern-bg mt-4 overflow-auto min-h-[420px]">
          <div class="text-center text-slate-400 p-8">
            <div class="text-4xl mb-3">📄</div>
            <p class="font-medium text-sm">No templates generated yet</p>
            <p class="text-xs mt-1">Select an .obj file and click "Generate Papercraft Templates" to preview sheets</p>
          </div>
        </div>

        <!-- Legend Swatches -->
        <div class="mt-4 pt-3 border-t border-slate-100 flex flex-wrap items-center justify-between text-[11px] text-slate-600 gap-3">
          <div class="flex items-center space-x-1.5">
            <span class="w-4 h-0.5 bg-black inline-block"></span>
            <span>Cut Line (Solid)</span>
          </div>
          <div class="flex items-center space-x-1.5">
            <span class="w-4 border-b-2 border-dashed border-red-600 inline-block"></span>
            <span>Mountain Fold (_ _ _)</span>
          </div>
          <div class="flex items-center space-x-1.5">
            <span class="w-4 border-b-2 border-dotted border-blue-600 inline-block"></span>
            <span>Valley Fold (_ . _ .)</span>
          </div>
          <div class="flex items-center space-x-1.5">
            <span class="w-3.5 h-3.5 bg-slate-200 border border-slate-400 inline-block text-[9px] text-center">▨</span>
            <span>Outward Tab (Cross-hatch)</span>
          </div>
        </div>

      </div>

    </div>

  </main>

  <script>
    let uploadedObjContent = null;
    let currentResultData = null;
    let activePageIndex = 0;

    // Synchronize Flap Size slider and number box
    const flapSlider = document.getElementById('flap-size');
    const flapNum = document.getElementById('flap-size-num');
    const flapVal = document.getElementById('flap-size-val');

    flapSlider.addEventListener('input', (e) => {
      flapNum.value = e.target.value;
      flapVal.innerText = `${e.target.value} mm`;
    });
    flapNum.addEventListener('input', (e) => {
      flapSlider.value = e.target.value;
      flapVal.innerText = `${e.target.value} mm`;
    });

    // File Drop Zone
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('obj-input');
    const fileNameLabel = document.getElementById('file-name-label');
    const modelStatus = document.getElementById('model-status');

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('border-indigo-500'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('border-indigo-500'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('border-indigo-500');
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length) handleFile(e.target.files[0]);
    });

    function handleFile(file) {
      if (!file.name.toLowerCase().endsWith('.obj')) {
        alert('Please select a Wavefront .obj file');
        return;
      }
      fileNameLabel.innerText = `Loaded: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
      modelStatus.innerText = 'Ready to unfold';
      modelStatus.className = 'text-emerald-600 font-medium';

      const reader = new FileReader();
      reader.onload = (e) => {
        uploadedObjContent = e.target.result;
        render3DPreview(uploadedObjContent);
      };
      reader.readAsText(file);
    }

    document.getElementById('btn-use-demo').addEventListener('click', () => {
      uploadedObjContent = "DEFAULT_DEMO";
      fileNameLabel.innerText = "Loaded: Geometric Polyhedron (Built-in Demo)";
      modelStatus.innerText = 'Ready to unfold';
      modelStatus.className = 'text-indigo-600 font-medium';
      render3DDemo();
    });

    // Three.js 3D Viewer Setup
    let scene, camera, renderer, currentMesh, controls;
    function init3D() {
      const container = document.getElementById('three-container');
      const w = container.clientWidth;
      const h = container.clientHeight;

      scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0f172a);

      camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000);
      camera.position.set(0, 50, 100);

      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(w, h);
      container.appendChild(renderer.domElement);

      const light1 = new THREE.DirectionalLight(0xffffff, 1.2);
      light1.position.set(10, 20, 15);
      scene.add(light1);
      const light2 = new THREE.AmbientLight(0x404040, 1.5);
      scene.add(light2);

      controls = new THREE.OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;

      function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
      }
      animate();
    }
    window.addEventListener('load', init3D);

    function render3DDemo() {
      document.getElementById('three-placeholder').style.display = 'none';
      if (currentMesh) scene.remove(currentMesh);
      const geom = new THREE.CylinderGeometry(25, 35, 50, 6);
      const mat = new THREE.MeshStandardMaterial({ color: 0x4f46e5, roughness: 0.3, flatShading: true });
      currentMesh = new THREE.Mesh(geom, mat);
      scene.add(currentMesh);
      camera.position.set(0, 40, 110);
    }

    function render3DPreview(objText) {
      document.getElementById('three-placeholder').style.display = 'none';
      if (currentMesh) scene.remove(currentMesh);
      const loader = new THREE.OBJLoader();
      try {
        const obj = loader.parse(objText);
        const mat = new THREE.MeshStandardMaterial({ color: 0x6366f1, roughness: 0.4, flatShading: true });
        obj.traverse((child) => { if (child.isMesh) child.material = mat; });
        
        // Center and scale object
        const box = new THREE.Box3().setFromObject(obj);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const scale = 50.0 / (maxDim || 1);
        obj.scale.set(scale, scale, scale);
        obj.position.sub(center.multiplyScalar(scale));

        currentMesh = obj;
        scene.add(currentMesh);
        camera.position.set(0, 30, 90);
      } catch (err) {
        console.error("Three.js OBJ parsing error:", err);
      }
    }

    // Generate Request
    const btnGenerate = document.getElementById('btn-generate');
    btnGenerate.addEventListener('click', async () => {
      btnGenerate.disabled = true;
      btnGenerate.innerHTML = `<span class="animate-spin text-lg">⚙️</span><span>Generating Templates...</span>`;

      const payload = {
        obj_content: uploadedObjContent || "DEFAULT_DEMO",
        flap_size_mm: parseFloat(flapSlider.value),
        paper_format: document.getElementById('paper-format').value,
        page_margin: parseFloat(document.getElementById('page-margin').value),
        variation: document.getElementById('variation-select').value,
        join_faces: document.getElementById('join-edges').checked
      };

      try {
        const resp = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.error) {
          alert("Generation Error: " + res.error);
        } else {
          currentResultData = res;
          displayResults(res);
        }
      } catch (err) {
        alert("Failed to communicate with generator: " + err);
      } finally {
        btnGenerate.disabled = false;
        btnGenerate.innerHTML = `<span>✨ Generate Papercraft Templates</span>`;
      }
    });

    function displayResults(data) {
      document.getElementById('output-meta').innerText = 
        `${data.total_pages} Sheet(s) • Format: ${data.format} • Flap Size: ${data.flap_size_mm} mm • Margin: ${data.margin_mm} mm`;

      const dlBox = document.getElementById('download-actions');
      dlBox.classList.remove('hidden');

      // Update downloads
      document.getElementById('dl-pdf').href = `/api/download?file=${encodeURIComponent(data.pdf_file)}`;
      document.getElementById('dl-svg').href = `/api/download?file=${encodeURIComponent(data.svg_files[0])}`;
      document.getElementById('dl-png').href = `/api/download?file=${encodeURIComponent(data.png_files[0])}`;

      // Build tabs
      const tabsBox = document.getElementById('page-tabs');
      tabsBox.innerHTML = '<span class="font-semibold text-slate-500 mr-2">Sheets:</span>';
      tabsBox.classList.remove('hidden');

      data.svg_files.forEach((file, idx) => {
        const btn = document.createElement('button');
        btn.className = `px-3 py-1 rounded text-xs font-semibold transition ${idx === 0 ? 'bg-indigo-600 text-white' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'}`;
        btn.innerText = `Sheet ${idx + 1}`;
        btn.addEventListener('click', () => selectPage(idx));
        tabsBox.appendChild(btn);
      });

      selectPage(0);
    }

    function selectPage(idx) {
      activePageIndex = idx;
      const tabs = document.getElementById('page-tabs').querySelectorAll('button');
      tabs.forEach((t, i) => {
        if (i === idx) {
          t.className = 'px-3 py-1 rounded text-xs font-semibold bg-indigo-600 text-white';
        } else {
          t.className = 'px-3 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700';
        }
      });

      // Update downloads for this page
      document.getElementById('dl-svg').href = `/api/download?file=${encodeURIComponent(currentResultData.svg_files[idx])}`;
      document.getElementById('dl-png').href = `/api/download?file=${encodeURIComponent(currentResultData.png_files[idx])}`;

      // Display SVG in preview area
      const previewArea = document.getElementById('preview-area');
      previewArea.innerHTML = `<div class="bg-white shadow-md rounded border border-slate-300 max-h-[550px] overflow-auto flex items-center justify-center p-2">${currentResultData.svg_contents[idx]}</div>`;
    }
  </script>

</body>
</html>
"""


class PapercraftWebHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler providing REST API and serving UI."""

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        params = urllib.parse.parse_qs(url.query)

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif path == "/api/download":
            filepath = params.get("file", [None])[0]
            if filepath and os.path.exists(filepath):
                fname = os.path.basename(filepath)
                ext = os.path.splitext(fname)[1].lower()
                mime = "application/pdf" if ext == ".pdf" else "image/svg+xml" if ext == ".svg" else "image/png"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(os.path.getsize(filepath)))
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "File not found")
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == "/api/generate":
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                data = json.loads(body)
                obj_content = data.get("obj_content", "DEFAULT_DEMO")
                flap_size_mm = float(data.get("flap_size_mm", 5.5))
                paper_format = data.get("paper_format", "LETTER")
                margin_mm = float(data.get("page_margin", 15.0))
                var_choice = data.get("variation", "1")

                # Parse model
                if obj_content == "DEFAULT_DEMO":
                    model = Model3D.create_default_model()
                else:
                    tmp_obj = os.path.join(tempfile.gettempdir(), "user_upload.obj")
                    with open(tmp_obj, 'w', encoding='utf-8') as f:
                        f.write(obj_content)
                    model = Model3D.load_obj(tmp_obj)

                # Page sizes
                if paper_format.upper() == "A4":
                    page_w, page_h = 210.0, 297.0
                else:
                    page_w, page_h = 215.9, 279.4

                # Unfold parts with user-specified flap size and auto-scale
                user_scale = data.get("scale", None)
                if user_scale is not None:
                    try:
                        user_scale = float(user_scale)
                        if user_scale <= 0:
                            user_scale = None
                    except (ValueError, TypeError):
                        user_scale = None

                join_faces = bool(data.get("join_faces", False))
                parts = unfold_model_to_parts(
                    model, target_scale=user_scale, flap_size_mm=flap_size_mm,
                    join_faces=join_faces, paper_format=paper_format, margin=margin_mm
                )
                pages = pack_parts_onto_pages(parts, page_w, page_h, margin=margin_mm, part_gap=5.0)

                out_temp = os.path.join(tempfile.gettempdir(), "pepakura_web_exports")
                os.makedirs(out_temp, exist_ok=True)

                var_id = int(var_choice) if var_choice in ("1", "2", "3", "4") else 1

                # Generate PDF
                pdf_path = os.path.join(out_temp, f"template_{paper_format}_{flap_size_mm}mm.pdf")
                export_pdf_pages(pages, page_w, page_h, pdf_path, variation=var_id)

                # Generate SVGs
                svg_files = []
                svg_contents = []
                for p_idx, page_parts in enumerate(pages):
                    sp = os.path.join(out_temp, f"template_page_{p_idx + 1}.svg")
                    export_svg_page(page_parts, p_idx + 1, len(pages), page_w, page_h, sp, variation=var_id)
                    svg_files.append(sp)
                    with open(sp, 'r', encoding='utf-8') as f:
                        svg_contents.append(f.read())

                # Generate 300 DPI PNGs
                png_files = render_pdf_to_300dpi_pngs(pdf_path, out_temp, "web_preview")

                res = {
                    "format": paper_format,
                    "flap_size_mm": flap_size_mm,
                    "margin_mm": margin_mm,
                    "total_pages": len(pages),
                    "pdf_file": pdf_path,
                    "svg_files": svg_files,
                    "svg_contents": svg_contents,
                    "png_files": png_files
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))

            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_error(404, "Not Found")


def run_web_server(port=PORT):
    server = HTTPServer(('0.0.0.0', port), PapercraftWebHandler)
    print("=" * 65)
    print(f"Pepakura Alternative Web Studio is running!")
    print(f"Open your browser and visit: http://localhost:{port}")
    print("Press Ctrl+C to stop the server.")
    print("=" * 65)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_web_server(port)

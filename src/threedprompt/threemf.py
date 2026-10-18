"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/threemf.py
Description: Multi-colour 3MF export (docs/print-tools-plan.md item 5):
    several STL parts -> one 3MF where each part prints with its own
    filament, for the Bambu AMS and the Elegoo CANVAS (Centauri Carbon).
    Written as plain XML (no lib3mf at runtime). The two slicer families
    read multi-part objects differently (checked against their 3MF
    loaders), so the layout is chosen with `slicer`:
      - "orca" (Bambu Studio, OrcaSlicer, Elegoo Slicer, Anycubic Slicer
        Next): an assembly object whose <components> point at one mesh
        object per part, and Metadata/model_settings.config with a <part>
        per component carrying its "extruder" (1-based filament slot).
        OrcaSlicer's bbs_3mf.cpp makes one volume per component and
        ignores triangle ranges.
      - "prusa" (PrusaSlicer): one mesh object holding every part's
        triangles, and Metadata/Slic3r_PE_model.config with a <volume
        firstid lastid> range per part carrying its "extruder".
        PrusaSlicer turns components into separate objects, which would
        drop a raised part (a QR code) off its plate.
    Either way each part also gets a 3MF materials-extension colour
    (<m:colorgroup>), which viewers show and Orca maps to filaments when
    no model_settings.config is present. With assembled=False every part
    is its own object laid out side by side, which both families read.
Inputs: write_3mf(out_path, parts, assembled, slicer) with parts as
    [{"name", "mesh" (a trimesh.Trimesh), "color" "#RRGGBB", "extruder" 1-16}].
Outputs: The .3mf file at out_path; a summary dict.
Troubleshooting:
    - Every part on filament 1 in Bambu/Orca/Elegoo: open the file (File >
      Open / drag onto an empty plate) rather than importing it into an
      existing project, which may keep only the geometry; or the file was
      exported with slicer="prusa".
    - PrusaSlicer shows the parts as separate objects, the top one dropped
      to the bed: export with slicer="prusa".
    - Parts land in the wrong slot: the extruder numbers are AMS / CANVAS
      slot numbers (1-based), not colours; reorder in the slicer.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from threedprompt.geometry import TemplateError

PALETTE = ["#F2F2F2", "#1C1C1F", "#E5484D", "#3D6BFF", "#2FAE5C", "#F0902A", "#B04AD8", "#D9B21B"]
SLICERS = ("orca", "prusa")
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>
"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""
_MODEL_OPEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
    ' xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">'
    '<metadata name="Application">3d-printing-model-prompt</metadata>'
)
_GAP_MM = 5.0  # between parts laid out side by side when not assembled


def _mesh_xml(vertices: np.ndarray, faces: np.ndarray) -> str:
    """<mesh> element (vertices then triangles)."""
    verts = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in vertices)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>"


def _check(parts: list[dict], slicer: str) -> list[dict]:
    """Validate parts (non-empty meshes, #RRGGBB colours, slots 1-16) and return normalised copies."""
    if slicer not in SLICERS:
        raise TemplateError(f"slicer must be one of {SLICERS}")
    if not parts:
        raise TemplateError("choose at least one part")
    out = []
    for p in parts:
        color = str(p["color"])
        if len(color) != 7 or color[0] != "#" or any(ch not in "0123456789abcdefABCDEF" for ch in color[1:]):
            raise TemplateError(f"colour {color!r} is not #RRGGBB")
        if not 1 <= int(p["extruder"]) <= 16:
            raise TemplateError("extruder (filament slot) must be 1-16")
        if len(p["mesh"].faces) == 0:
            raise TemplateError(f"part {p['name']!r} has no triangles")
        out.append(
            {"name": str(p["name"]), "mesh": p["mesh"].copy(), "color": color.upper(), "extruder": int(p["extruder"])}
        )
    return out


def _colour_groups(parts: list[dict]) -> str:
    """One <m:colorgroup> per part, ids 1..n (object pid = part index + 1)."""
    return "".join(
        f'<m:colorgroup id="{i + 1}"><m:color color="{p["color"]}FF"/></m:colorgroup>' for i, p in enumerate(parts)
    )


def write_3mf(out_path: Path, parts: list[dict], assembled: bool = True, slicer: str = "orca") -> dict:
    """
    Write a multi-colour 3MF.

    Args:
        out_path: destination .3mf file.
        parts: [{"name": str, "mesh": trimesh.Trimesh, "color": "#RRGGBB", "extruder": int}].
        assembled: True = one object made of all parts in their own positions;
            False = one object per part, laid out side by side on the bed.
        slicer: "orca" (Bambu Studio / OrcaSlicer / Elegoo Slicer) or "prusa"
            (PrusaSlicer); only matters for assembled files.
    Returns:
        {"objects", "parts", "triangles", "slicer", "filaments": [{"extruder", "color", "parts"}]}.
    Raises:
        TemplateError: no parts, an empty mesh, a bad colour, slot or slicer.
    """
    parts = _check(parts, slicer)
    meshes = [p["mesh"] for p in parts]
    if assembled:
        lo = np.min([m.bounds[0] for m in meshes], axis=0)
        for m in meshes:  # whole assembly on the bed (z=0), near the origin, positions kept relative
            m.apply_translation(-lo)
    else:
        x = 0.0
        for m in meshes:
            m.apply_translation([x - m.bounds[0][0], -m.bounds[0][1], -m.bounds[0][2]])
            x = m.bounds[1][0] + _GAP_MM

    n = len(parts)
    resources = [_colour_groups(parts)]
    bambu = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>']
    prusa = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>']
    name = " + ".join(p["name"] for p in parts)
    first_id = n + 1  # object ids follow the colour group ids

    if assembled and slicer == "prusa":
        oid = first_id
        offsets = np.cumsum([0] + [len(m.vertices) for m in meshes])
        verts = np.concatenate([m.vertices for m in meshes])
        faces = np.concatenate([m.faces + offsets[i] for i, m in enumerate(meshes)])
        resources.append(f'<object id="{oid}" type="model" name={quoteattr(name)} pid="1" pindex="0">')
        resources.append(_mesh_xml(verts, faces) + "</object>")
        build_ids = [oid]
        prusa.append(f'<object id="{oid}" instances_count="1">')
        prusa.append(f'<metadata type="object" key="name" value={quoteattr(name)}/>')
        first_tri = 0
        for p, m in zip(parts, meshes, strict=True):
            last_tri = first_tri + len(m.faces) - 1
            prusa.append(
                f'<volume firstid="{first_tri}" lastid="{last_tri}">'
                f'<metadata type="volume" key="name" value={quoteattr(p["name"])}/>'
                f'<metadata type="volume" key="extruder" value="{p["extruder"]}"/></volume>'
            )
            first_tri = last_tri + 1
        prusa.append("</object>")
    else:
        for i, (p, m) in enumerate(zip(parts, meshes, strict=True)):
            resources.append(
                f'<object id="{first_id + i}" type="model" name={quoteattr(p["name"])} pid="{i + 1}" pindex="0">'
                f"{_mesh_xml(m.vertices, m.faces)}</object>"
            )
        if assembled:  # orca
            assembly_id = first_id + n
            comps = "".join(f'<component objectid="{first_id + i}"/>' for i in range(n))
            resources.append(
                f'<object id="{assembly_id}" type="model" name={quoteattr(name)}>'
                f"<components>{comps}</components></object>"
            )
            build_ids = [assembly_id]
            bambu.append(f'<object id="{assembly_id}"><metadata key="name" value={quoteattr(name)}/>')
            bambu.append(f'<metadata key="extruder" value="{parts[0]["extruder"]}"/>')
            for i, p in enumerate(parts):
                bambu.append(
                    f'<part id="{first_id + i}" subtype="normal_part">'
                    f'<metadata key="name" value={quoteattr(p["name"])}/>'
                    f'<metadata key="extruder" value="{p["extruder"]}"/></part>'
                )
            bambu.append("</object>")
        else:
            build_ids = [first_id + i for i in range(n)]
            for i, (p, m) in enumerate(zip(parts, meshes, strict=True)):
                oid = first_id + i
                bambu.append(
                    f'<object id="{oid}"><metadata key="name" value={quoteattr(p["name"])}/>'
                    f'<metadata key="extruder" value="{p["extruder"]}"/></object>'
                )
                prusa.append(
                    f'<object id="{oid}" instances_count="1">'
                    f'<metadata type="object" key="name" value={quoteattr(p["name"])}/>'
                    f'<metadata type="object" key="extruder" value="{p["extruder"]}"/>'
                    f'<volume firstid="0" lastid="{len(m.faces) - 1}"/></object>'
                )
    bambu.append("</config>\n")
    prusa.append("</config>\n")
    build = "".join(f'<item objectid="{oid}"/>' for oid in build_ids)
    model = f'{_MODEL_OPEN}<resources>{"".join(resources)}</resources><build>{build}</build></model>\n'

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", model)
        if not (assembled and slicer == "prusa"):
            zf.writestr("Metadata/model_settings.config", "".join(bambu))
        if not (assembled and slicer == "orca"):
            zf.writestr("Metadata/Slic3r_PE_model.config", "".join(prusa))

    filaments: dict[int, dict] = {}
    for p in parts:
        f = filaments.setdefault(p["extruder"], {"extruder": p["extruder"], "color": p["color"], "parts": []})
        f["parts"].append(p["name"])
    return {
        "objects": len(build_ids),
        "parts": n,
        "triangles": int(sum(len(m.faces) for m in meshes)),
        "slicer": slicer,
        "filaments": sorted(filaments.values(), key=lambda f: f["extruder"]),
    }

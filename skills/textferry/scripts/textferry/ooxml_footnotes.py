# SPDX-License-Identifier: AGPL-3.0-only
"""带完整脚注样式、编号设置、section级 footnotePr 与脚注正文格式的 Word 文档。"""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from typing import Iterable, List
import xml.etree.ElementTree as ET


NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("w", NS_W)
ET.register_namespace("r", NS_R)
ET.register_namespace("", NS_CT)


@dataclass(frozen=True)
class OoxmlFootnote:
    id: int
    key: str
    text: str


def inject_footnotes(
    input_docx: str,
    output_docx: str,
    footnotes: Iterable[OoxmlFootnote],
    *,
    font_cjk: str = "宋体",
    font_latin: str = "Times New Roman",
    size_pt: float = 9,
    restart_mode: str = "eachPage",
    number_format: str = "decimalEnclosedCircle",
) -> None:
    footnote_list = list(footnotes)
    if not footnote_list:
        if os.path.abspath(input_docx) != os.path.abspath(output_docx):
            shutil.copyfile(input_docx, output_docx)
        return

    footnotes_xml = _build_footnotes_xml(
        footnote_list,
        font_cjk=font_cjk,
        font_latin=font_latin,
        size_pt=size_pt,
    )

    with zipfile.ZipFile(input_docx, "r") as zin:
        temp_path = output_docx
        if os.path.abspath(input_docx) == os.path.abspath(output_docx):
            temp_path = output_docx + ".tmp"
        with zipfile.ZipFile(temp_path, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/footnotes.xml":
                    data = footnotes_xml
                elif item.filename == "word/document.xml":
                    data = _ensure_document_footnote_pr(
                        data,
                        restart_mode=restart_mode,
                        number_format=number_format,
                    )
                elif item.filename == "[Content_Types].xml":
                    data = _ensure_content_types(data)
                elif item.filename == "word/_rels/document.xml.rels":
                    data = _ensure_document_rels(data)
                elif item.filename == "word/settings.xml":
                    data = _ensure_settings(
                        data,
                        restart_mode=restart_mode,
                        number_format=number_format,
                    )
                elif item.filename == "word/styles.xml":
                    data = _ensure_styles(data, size_pt=size_pt)
                zout.writestr(item, data)
            if "word/footnotes.xml" not in zin.namelist():
                zout.writestr("word/footnotes.xml", footnotes_xml)
        if temp_path != output_docx:
            os.replace(temp_path, output_docx)


def _ensure_content_types(content_xml: bytes) -> bytes:
    root = ET.fromstring(content_xml)
    footnote_part = "/word/footnotes.xml"
    for override in root.findall(f"{{{NS_CT}}}Override"):
        if override.get("PartName") == footnote_part:
            return content_xml
    ET.SubElement(
        root,
        f"{{{NS_CT}}}Override",
        PartName=footnote_part,
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ensure_document_rels(rels_xml: bytes) -> bytes:
    root = ET.fromstring(rels_xml)
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
    for rel in root.findall(f"{{{NS_REL}}}Relationship"):
        if rel.get("Type") == rel_type:
            return rels_xml

    existing_ids = {rel.get("Id") for rel in root.findall(f"{{{NS_REL}}}Relationship")}
    next_id = 1
    while f"rId{next_id}" in existing_ids:
        next_id += 1

    ET.SubElement(
        root,
        f"{{{NS_REL}}}Relationship",
        Id=f"rId{next_id}",
        Type=rel_type,
        Target="footnotes.xml",
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ensure_settings(settings_xml: bytes, *, restart_mode: str, number_format: str) -> bytes:
    root = ET.fromstring(settings_xml)
    footnote_pr = root.find(f"{{{NS_W}}}footnotePr")
    if footnote_pr is None:
        footnote_pr = ET.SubElement(root, f"{{{NS_W}}}footnotePr")
    num_fmt = footnote_pr.find(f"{{{NS_W}}}numFmt")
    if num_fmt is None:
        num_fmt = ET.SubElement(footnote_pr, f"{{{NS_W}}}numFmt")
    num_fmt.set(f"{{{NS_W}}}val", number_format)
    num_restart = footnote_pr.find(f"{{{NS_W}}}numRestart")
    if num_restart is None:
        num_restart = ET.SubElement(footnote_pr, f"{{{NS_W}}}numRestart")
    num_restart.set(f"{{{NS_W}}}val", restart_mode)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ensure_document_footnote_pr(document_xml: bytes, *, restart_mode: str, number_format: str) -> bytes:
    root = ET.fromstring(document_xml)
    body = root.find(f"{{{NS_W}}}body")
    if body is None:
        return document_xml

    section_nodes: List[ET.Element] = []
    body_sect_pr = body.find(f"{{{NS_W}}}sectPr")
    if body_sect_pr is not None:
        section_nodes.append(body_sect_pr)
    for paragraph in body.findall(f"{{{NS_W}}}p"):
        ppr = paragraph.find(f"{{{NS_W}}}pPr")
        if ppr is None:
            continue
        p_sect_pr = ppr.find(f"{{{NS_W}}}sectPr")
        if p_sect_pr is not None:
            section_nodes.append(p_sect_pr)

    if not section_nodes:
        section_nodes.append(ET.SubElement(body, f"{{{NS_W}}}sectPr"))

    seen = set()
    for sect_pr in section_nodes:
        node_id = id(sect_pr)
        if node_id in seen:
            continue
        seen.add(node_id)
        footnote_pr = sect_pr.find(f"{{{NS_W}}}footnotePr")
        if footnote_pr is None:
            footnote_pr = ET.SubElement(sect_pr, f"{{{NS_W}}}footnotePr")
        num_fmt = footnote_pr.find(f"{{{NS_W}}}numFmt")
        if num_fmt is None:
            num_fmt = ET.SubElement(footnote_pr, f"{{{NS_W}}}numFmt")
        num_fmt.set(f"{{{NS_W}}}val", number_format)
        num_restart = footnote_pr.find(f"{{{NS_W}}}numRestart")
        if num_restart is None:
            num_restart = ET.SubElement(footnote_pr, f"{{{NS_W}}}numRestart")
        num_restart.set(f"{{{NS_W}}}val", restart_mode)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ensure_styles(styles_xml: bytes, *, size_pt: float) -> bytes:
    root = ET.fromstring(styles_xml)
    existing = {style.get(f"{{{NS_W}}}styleId") for style in root.findall(f"{{{NS_W}}}style")}

    if "FootnoteText" not in existing:
        style = ET.SubElement(root, f"{{{NS_W}}}style")
        style.set(f"{{{NS_W}}}type", "paragraph")
        style.set(f"{{{NS_W}}}styleId", "FootnoteText")
        name = ET.SubElement(style, f"{{{NS_W}}}name")
        name.set(f"{{{NS_W}}}val", "footnote text")
        based_on = ET.SubElement(style, f"{{{NS_W}}}basedOn")
        based_on.set(f"{{{NS_W}}}val", "Normal")
        ET.SubElement(style, f"{{{NS_W}}}semiHidden")
        ET.SubElement(style, f"{{{NS_W}}}unhideWhenUsed")
        ui_priority = ET.SubElement(style, f"{{{NS_W}}}uiPriority")
        ui_priority.set(f"{{{NS_W}}}val", "99")
        ppr = ET.SubElement(style, f"{{{NS_W}}}pPr")
        snap = ET.SubElement(ppr, f"{{{NS_W}}}snapToGrid")
        snap.set(f"{{{NS_W}}}val", "0")
        spacing = ET.SubElement(ppr, f"{{{NS_W}}}spacing")
        spacing.set(f"{{{NS_W}}}before", "0")
        spacing.set(f"{{{NS_W}}}after", "0")
        spacing.set(f"{{{NS_W}}}line", "240")
        spacing.set(f"{{{NS_W}}}lineRule", "auto")
        rpr = ET.SubElement(style, f"{{{NS_W}}}rPr")
        size_val = str(int(round(size_pt * 2)))
        sz = ET.SubElement(rpr, f"{{{NS_W}}}sz")
        sz.set(f"{{{NS_W}}}val", size_val)
        sz_cs = ET.SubElement(rpr, f"{{{NS_W}}}szCs")
        sz_cs.set(f"{{{NS_W}}}val", size_val)

    if "FootnoteReference" not in existing:
        style = ET.SubElement(root, f"{{{NS_W}}}style")
        style.set(f"{{{NS_W}}}type", "character")
        style.set(f"{{{NS_W}}}styleId", "FootnoteReference")
        name = ET.SubElement(style, f"{{{NS_W}}}name")
        name.set(f"{{{NS_W}}}val", "footnote reference")
        based_on = ET.SubElement(style, f"{{{NS_W}}}basedOn")
        based_on.set(f"{{{NS_W}}}val", "DefaultParagraphFont")
        ET.SubElement(style, f"{{{NS_W}}}semiHidden")
        ET.SubElement(style, f"{{{NS_W}}}unhideWhenUsed")
        ui_priority = ET.SubElement(style, f"{{{NS_W}}}uiPriority")
        ui_priority.set(f"{{{NS_W}}}val", "99")
        rpr = ET.SubElement(style, f"{{{NS_W}}}rPr")
        valign = ET.SubElement(rpr, f"{{{NS_W}}}vertAlign")
        valign.set(f"{{{NS_W}}}val", "superscript")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _build_footnotes_xml(
    footnotes: List[OoxmlFootnote],
    *,
    font_cjk: str,
    font_latin: str,
    size_pt: float,
) -> bytes:
    root = ET.Element(f"{{{NS_W}}}footnotes")
    root.set("xmlns:r", NS_R)

    _add_separator(root, separator_type="separator", elem="separator", footnote_id="0")
    _add_separator(root, separator_type="continuationSeparator", elem="continuationSeparator", footnote_id="1")

    for footnote in footnotes:
        _add_footnote(root, footnote, font_cjk=font_cjk, font_latin=font_latin, size_pt=size_pt)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _add_separator(root: ET.Element, separator_type: str, elem: str, footnote_id: str) -> None:
    footnote = ET.SubElement(root, f"{{{NS_W}}}footnote")
    footnote.set(f"{{{NS_W}}}type", separator_type)
    footnote.set(f"{{{NS_W}}}id", footnote_id)
    paragraph = ET.SubElement(footnote, f"{{{NS_W}}}p")
    run = ET.SubElement(paragraph, f"{{{NS_W}}}r")
    ET.SubElement(run, f"{{{NS_W}}}{elem}")


def _add_footnote(
    root: ET.Element,
    footnote: OoxmlFootnote,
    *,
    font_cjk: str,
    font_latin: str,
    size_pt: float,
) -> None:
    footnote_el = ET.SubElement(root, f"{{{NS_W}}}footnote")
    footnote_el.set(f"{{{NS_W}}}id", str(footnote.id))

    paragraph = ET.SubElement(footnote_el, f"{{{NS_W}}}p")
    ppr = ET.SubElement(paragraph, f"{{{NS_W}}}pPr")
    pstyle = ET.SubElement(ppr, f"{{{NS_W}}}pStyle")
    pstyle.set(f"{{{NS_W}}}val", "FootnoteText")
    spacing = ET.SubElement(ppr, f"{{{NS_W}}}spacing")
    spacing.set(f"{{{NS_W}}}before", "0")
    spacing.set(f"{{{NS_W}}}after", "0")
    spacing.set(f"{{{NS_W}}}line", "240")
    spacing.set(f"{{{NS_W}}}lineRule", "auto")

    ref_run = ET.SubElement(paragraph, f"{{{NS_W}}}r")
    ref_rpr = ET.SubElement(ref_run, f"{{{NS_W}}}rPr")
    ref_style = ET.SubElement(ref_rpr, f"{{{NS_W}}}rStyle")
    ref_style.set(f"{{{NS_W}}}val", "FootnoteReference")
    ET.SubElement(ref_run, f"{{{NS_W}}}footnoteRef")

    text = " " + footnote.text.strip() if footnote.text.strip() else " "
    size_val = str(int(round(size_pt * 2)))
    _add_text_run(paragraph, text, font_cjk=font_cjk, font_latin=font_latin, size_val=size_val)


def _add_text_run(
    paragraph: ET.Element,
    text: str,
    *,
    font_cjk: str,
    font_latin: str,
    size_val: str,
) -> None:
    run = ET.SubElement(paragraph, f"{{{NS_W}}}r")
    rpr = ET.SubElement(run, f"{{{NS_W}}}rPr")
    rfonts = ET.SubElement(rpr, f"{{{NS_W}}}rFonts")
    rfonts.set(f"{{{NS_W}}}ascii", font_latin)
    rfonts.set(f"{{{NS_W}}}hAnsi", font_latin)
    rfonts.set(f"{{{NS_W}}}eastAsia", font_cjk)
    sz = ET.SubElement(rpr, f"{{{NS_W}}}sz")
    sz.set(f"{{{NS_W}}}val", size_val)
    sz_cs = ET.SubElement(rpr, f"{{{NS_W}}}szCs")
    sz_cs.set(f"{{{NS_W}}}val", size_val)

    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if idx > 0:
            ET.SubElement(run, f"{{{NS_W}}}br")
        text_el = ET.SubElement(run, f"{{{NS_W}}}t")
        if line.startswith(" ") or line.endswith(" "):
            text_el.set(f"{{{XML_NS}}}space", "preserve")
        text_el.text = line

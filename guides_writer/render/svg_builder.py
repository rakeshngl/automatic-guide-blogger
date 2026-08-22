import html
import logging

from guides_writer.render.schema import DiagramSpec

logger = logging.getLogger(__name__)

NODE_W = 170
NODE_H = 48
COL_GAP = 40
ROW_GAP = 24
GROUP_PAD_X = 14
GROUP_LABEL_H = 30
MARGIN = 20


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def build_svg(spec: DiagramSpec) -> str:
    group_ids = [g.id for g in spec.groups]

    def column_of(node) -> int:
        if node.group in group_ids:
            return 1 + group_ids.index(node.group)
        return 0

    ungrouped = [n for n in spec.nodes if column_of(n) == 0]
    columns: list[list] = [[] for _ in range(1 + len(group_ids))]
    for node in spec.nodes:
        columns[column_of(node)].append(node)

    used = [col for col in columns]
    col_x: list[int] = []
    x = MARGIN
    heights: list[int] = []
    for col in used:
        h = max(len(col), 1) * NODE_H + max(len(col) - 1, 0) * ROW_GAP
        heights.append(h)
        col_x.append(x)
        x += NODE_W + COL_GAP
    total_w = x - COL_GAP + MARGIN

    content_h = max(heights + [NODE_H * 2])
    top_pad = MARGIN + (GROUP_LABEL_H if any(g.style != "plain" for g in spec.groups) else 0)
    total_h = content_h + top_pad + MARGIN

    node_pos: dict[str, tuple[int, int]] = {}
    for ci, col in enumerate(used):
        y = top_pad + (content_h - heights[ci]) / 2
        for node in col:
            node_pos[node.id] = (int(col_x[ci]), int(y))
            y += NODE_H + ROW_GAP

    parts: list[str] = []
    parts.append(
        f'<svg width="100%" height="{total_h}" viewBox="0 0 {total_w} {total_h}" '
        'xmlns="http://www.w3.org/2000/svg" role="img">'
    )
    parts.append(
        '<defs><marker id="gw-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" class="link-arrow"/></marker></defs>'
    )

    for gi, group in enumerate(spec.groups, start=1):
        members = [(nid, pos) for nid, pos in node_pos.items() if any(n.id == nid and n.group == group.id for n in spec.nodes)]
        if not members or group.style == "plain":
            continue
        xs = [p[0] for _, p in members]
        ys = [p[1] for _, p in members]
        box_x = min(xs) - GROUP_PAD_X
        box_y = min(ys) - GROUP_LABEL_H + 4
        box_w = NODE_W + 2 * GROUP_PAD_X
        box_h = max(ys) - min(ys) + NODE_H + GROUP_LABEL_H - 8
        stroke = "#b91c1c" if group.style == "cloud" else "var(--accent)"
        label_fill = "#b91c1c" if group.style == "cloud" else "var(--accent)"
        parts.append(
            f'<rect x="{box_x}" y="{int(box_y)}" width="{box_w}" height="{int(box_h)}" '
            f'fill="none" stroke="{stroke}" stroke-width="2.5" rx="10" '
            'stroke-dasharray="6,6"/>'
        )
        parts.append(
            f'<text x="{box_x + box_w / 2}" y="{int(box_y) + 18}" text-anchor="middle" '
            f'style="font-family:\'Plus Jakarta Sans\',sans-serif;font-size:11px;'
            f'font-weight:700;letter-spacing:0.5px;fill:{label_fill}">{_esc(group.label.upper())}</text>'
        )

    for edge in spec.edges:
        src = node_pos.get(edge.from_node)
        dst = node_pos.get(edge.to_node)
        if not src or not dst:
            logger.warning("svg_edge_skipped unknown=%s/%s", edge.from_node, edge.to_node)
            continue
        sx, sy = src[0] + NODE_W // 2, src[1] + NODE_H // 2
        dx, dy = dst[0] + NODE_W // 2, dst[1] + NODE_H // 2
        if abs(dx - sx) >= abs(dy - sy):
            x1 = sx + NODE_W // 2 if dx > sx else sx - NODE_W // 2
            x2 = dx - NODE_W // 2 if dx > sx else dx + NODE_W // 2
            y1, y2 = sy, dy
        else:
            y1 = sy + NODE_H // 2 if dy > sy else sy - NODE_H // 2
            y2 = dy - NODE_H // 2 if dy > sy else dy + NODE_H // 2
            x1, x2 = sx, dx
        marker_end = 'marker-end="url(#gw-arrow)"'
        marker_start = 'marker-start="url(#gw-arrow)"' if edge.bidirectional else ""
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="link" '
            f'{marker_start} {marker_end}/>'
        )
        if edge.label:
            parts.append(
                f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2 - 6:.0f}" text-anchor="middle" '
                'style="font-family:\'Plus Jakarta Sans\',sans-serif;font-size:11px;'
                f'fill:#6b7280">{_esc(edge.label)}</text>'
            )

    for node in spec.nodes:
        pos = node_pos.get(node.id)
        if not pos:
            continue
        nx, ny = pos
        cls = "node-tech" if node.style in ("tech", "danger") else "node"
        style_attr = 'style="stroke:#b91c1c"' if node.style == "danger" else ""
        parts.append(f'<rect x="{nx}" y="{ny}" width="{NODE_W}" height="{NODE_H}" rx="10" class="{cls}" {style_attr}/>')
        if node.sublabel:
            parts.append(
                f'<text x="{nx + NODE_W / 2}" y="{ny + 21}" class="node-text">{_esc(node.label)}</text>'
            )
            parts.append(
                f'<text x="{nx + NODE_W / 2}" y="{ny + 37}" class="node-subtext">{_esc(node.sublabel)}</text>'
            )
        else:
            parts.append(
                f'<text x="{nx + NODE_W / 2}" y="{ny + 29}" class="node-text">{_esc(node.label)}</text>'
            )

    parts.append("</svg>")
    svg = "\n".join(parts)
    logger.info("svg_built nodes=%d edges=%d size=%dx%d", len(spec.nodes), len(spec.edges), total_w, total_h)
    return svg

import { TOKENS } from "../styles/atlassianTokens";
import type { TreeNode } from "../types";

interface Props {
  node: TreeNode;
  depth: number;
  selectedId: string | null;
  onSelect: (node: TreeNode) => void;
}

export function TreeNodeView({ node, depth, selectedId, onSelect }: Props) {
  const active = selectedId === node.id;
  const indent = depth * 12 + 8;

  const labelColor = node.kind === "ticket" ? TOKENS.textPrimary : TOKENS.textSecondary;
  const fontWeight = node.kind === "root" || node.kind === "group" ? 600 : 500;
  const fontSize = node.kind === "root" ? 13 : node.kind === "ticket" ? 12 : 12;

  return (
    <div>
      <button
        type="button"
        onClick={() => onSelect(node)}
        className="block w-full text-left transition-colors"
        style={{
          paddingLeft: indent,
          paddingRight: 8,
          paddingTop: 5,
          paddingBottom: 5,
          background: active ? TOKENS.blueLight : "transparent",
          color: active ? TOKENS.blueDark : labelColor,
          borderLeft: active ? `2px solid ${TOKENS.blue}` : "2px solid transparent",
          fontWeight,
          fontSize,
        }}
      >
        <span
          className="mr-1 inline-block"
          style={{ color: TOKENS.textMuted, fontSize: 10 }}
        >
          {kindGlyph(node.kind)}
        </span>
        {node.label}
      </button>
      {node.children?.map((child) => (
        <TreeNodeView
          key={child.id}
          node={child}
          depth={depth + 1}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function kindGlyph(kind: TreeNode["kind"]): string {
  switch (kind) {
    case "root":
      return "▣";
    case "group":
      return "▾";
    case "lane":
      return "▸";
    case "queue":
      return "⤳";
    case "ticket":
      return "·";
    default:
      return "·";
  }
}

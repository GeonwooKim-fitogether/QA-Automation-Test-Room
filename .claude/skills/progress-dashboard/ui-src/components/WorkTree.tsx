import { TOKENS } from "../styles/atlassianTokens";
import { TreeNodeView } from "./TreeNode";
import { DocsPanel } from "./DocsPanel";
import type { DocLink, TreeNode } from "../types";

interface Props {
  root: TreeNode;
  selectedId: string | null;
  selectedNode: TreeNode | null;
  onSelect: (node: TreeNode) => void;
  docs?: DocLink[];
}

export function WorkTree({ root, selectedId, selectedNode, onSelect, docs }: Props) {
  return (
    <aside
      className="flex h-full flex-col"
      style={{
        background: TOKENS.bgWhite,
        borderRight: `1px solid ${TOKENS.border}`,
      }}
      aria-label="Work tree"
    >
      <div
        className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wide"
        style={{
          color: TOKENS.textMuted,
          borderBottom: `1px solid ${TOKENS.divider}`,
          background: TOKENS.bg,
        }}
      >
        Work Tree
      </div>

      {/* Top: scrolling tree */}
      <div className="flex-1 overflow-y-auto py-1" style={{ minHeight: 0 }}>
        <TreeNodeView
          node={root}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      </div>

      {/* Bottom: docs panel pinned to the bottom of the sidebar */}
      <div
        style={{
          maxHeight: "40%",
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        <DocsPanel selectedNode={selectedNode} docs={docs} />
      </div>
    </aside>
  );
}

import { yaml } from "@codemirror/lang-yaml";
import { oneDark } from "@codemirror/theme-one-dark";
import CodeMirror from "@uiw/react-codemirror";

import { cn } from "@/lib/utils";

interface EditorPaneProps {
  /** 受控值(编辑器内容 = 屏层 doc.content) */
  value: string;
  /** 内容变更(屏层只更新草稿,不自动保存) */
  onChange: (value: string) => void;
  className?: string;
}

/**
 * CodeMirror 封装(@uiw/react-codemirror wrapper 路线,任务决议 5):
 * YAML 语法高亮 + oneDark 主题(与五屏暗色观感协调,不引重主题包)+ 行号。
 * 受控 value/onChange;快捷键(Cmd+S 保存)由屏层监听 window,编辑器不抢。
 */
export function EditorPane({ value, onChange, className }: EditorPaneProps) {
  return (
    <div className={cn("min-h-0 overflow-hidden rounded-md", className)} data-testid="yaml-editor">
      <CodeMirror
        value={value}
        height="100%"
        theme={oneDark}
        extensions={[yaml()]}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: true,
          autocompletion: true,
          bracketMatching: true,
          closeBrackets: true,
        }}
        onChange={(next) => onChange(next)}
      />
    </div>
  );
}

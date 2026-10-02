import * as React from "react";

interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}

/** 五屏共用页头:标题 + 一行中文说明(右侧留动作位,C 阶段用)。 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-1">
      <div className="flex flex-col gap-1">
        <h1 className="text-base font-semibold text-foreground">{title}</h1>
        {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

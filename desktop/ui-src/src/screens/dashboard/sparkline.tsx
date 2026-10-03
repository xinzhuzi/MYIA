import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * 自绘 SVG sparkline(D5 决议⑥:零依赖,不引 recharts;Linear/Vercel 的迷你
 * 趋势本就是简单折线+渐变填充)。~60 行可复用到多卡:本屏趋势卡消费,
 * 日后行情卡可直接复用。坐标自算(等距 x + max 归一 y),全零 = 居中平线
 * 不除零;单点 = 居中一点。数字画布 viewBox,外层用 className 控制实宽。
 */

export interface SparklineProps {
  /** 旧→新等长序列(补零由调用侧 fillDailyCounts 负责;空数组 = 空画布) */
  values: number[];
  /** viewBox 宽(默认 260,与卡宽解耦) */
  width?: number;
  /** viewBox 高(默认 48) */
  height?: number;
  /** 四周留白(px;线不贴边) */
  pad?: number;
  /** 折线色类(默认 stroke-primary) */
  strokeClassName?: string;
  /** 折线下渐变填充(D5:折线+渐变填充;默认开) */
  area?: boolean;
  /** building 态呼吸:末点亮起点(teardown-vercel-dashboard #6「building 态可 pulse」) */
  pulse?: boolean;
  className?: string;
  /** 无障碍名(图表语义;必填,R4) */
  "aria-label": string;
  "data-testid"?: string;
}

interface SparkPoint {
  x: number;
  y: number;
}

/** 等距 x + 按 max 归一 y;全零 = 居中平线(max=0 不除零),单点居中。 */
function sparkPoints(values: number[], width: number, height: number, pad: number): SparkPoint[] {
  if (values.length === 0 || width <= pad * 2 || height <= pad * 2) return [];
  const max = Math.max(...values, 0);
  const spanX = width - pad * 2;
  const spanY = height - pad * 2;
  return values.map((value, index) => ({
    x: values.length === 1 ? width / 2 : pad + (spanX * index) / (values.length - 1),
    y: max === 0 ? height / 2 : pad + spanY * (1 - value / max),
  }));
}

/** 自绘 SVG sparkline:折线(+可选渐变填充+末点呼吸)。 */
export function Sparkline({
  values,
  width = 260,
  height = 48,
  pad = 3,
  strokeClassName = "stroke-primary",
  area = true,
  pulse = false,
  className,
  "aria-label": ariaLabel,
  "data-testid": dataTestId,
}: SparklineProps) {
  const gradientId = useId();
  const points = sparkPoints(values, width, height, pad);
  const line = points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const baseline = height - pad;
  const last = points.length > 0 ? points[points.length - 1] : null;

  return (
    <svg
      data-testid={dataTestId}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block h-12 w-full", className)}
      role="img"
      aria-label={ariaLabel}
      preserveAspectRatio="none"
    >
      {area && points.length > 1 ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity="0.25" />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon
            points={`${line} ${points[points.length - 1].x.toFixed(1)},${baseline} ${points[0].x.toFixed(1)},${baseline}`}
            fill={`url(#${gradientId})`}
            className="text-primary"
          />
        </>
      ) : null}
      {points.length > 0 ? (
        <polyline
          points={line}
          fill="none"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          className={strokeClassName}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {pulse && last ? (
        <circle
          cx={last.x}
          cy={last.y}
          r="2.5"
          className={cn("animate-pulse", strokeClassName.replace("stroke-", "fill-"))}
        />
      ) : null}
    </svg>
  );
}

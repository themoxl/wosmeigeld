type ProgressTone = "positive" | "warning" | "highlight" | "neutral";

interface ProgressBarProps {
  value: number;
  max?: number;
  tone?: ProgressTone;
  showValue?: boolean;
  valueSuffix?: string;
  className?: string;
  testId?: string;
}

interface StackedSegment {
  label: string;
  value: number;
  tone: ProgressTone;
}

interface StackedProgressBarProps {
  segments: StackedSegment[];
  className?: string;
  testId?: string;
}

function clampPercentage(value: number): number {
  return Math.min(100, Math.max(0, value));
}

export function ProgressBar({
  value,
  max = 100,
  tone = "positive",
  showValue = false,
  valueSuffix = "%",
  className = "",
  testId,
}: ProgressBarProps) {
  const percentage = clampPercentage(max <= 0 ? 0 : (value / max) * 100);

  return (
    <div className={`progress-inline ${className}`.trim()} data-testid={testId}>
      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={Math.round(percentage)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`progress-bar-fill progress-bar-fill--${tone}`}
          style={{ width: `${percentage}%` }}
        />
      </div>
      {showValue && (
        <span className="progress-value">
          {Math.round(percentage)}
          {valueSuffix}
        </span>
      )}
    </div>
  );
}

export function StackedProgressBar({ segments, className = "", testId }: StackedProgressBarProps) {
  const total = segments.reduce((sum, segment) => sum + Math.max(0, segment.value), 0);
  const safeTotal = total > 0 ? total : 1;

  return (
    <div className={`stacked-progress ${className}`.trim()} data-testid={testId}>
      {segments.map((segment) => {
        const percentage = clampPercentage((Math.max(0, segment.value) / safeTotal) * 100);
        return (
          <div
            key={segment.label}
            className={`stacked-progress-segment stacked-progress-segment--${segment.tone}`}
            style={{ width: `${percentage}%` }}
            title={`${segment.label}: ${segment.value.toFixed(1)}%`}
          >
            {percentage >= 14 ? `${segment.label} ${Math.round(segment.value)}%` : ""}
          </div>
        );
      })}
    </div>
  );
}

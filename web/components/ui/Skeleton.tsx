import React from 'react';

export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`animate-pulse rounded-md bg-muted ${className}`}
      {...props}
    />
  );
}

export function TableSkeleton({ rows = 5, columns = 5 }) {
  return (
    <div className="w-full space-y-3">
      <div className="flex w-full justify-between space-x-4 border-b pb-3 border-gray-800">
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton key={`h-${i}`} className="h-4 w-[100px] bg-gray-800" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={`r-${i}`} className="flex w-full justify-between space-x-4">
          {Array.from({ length: columns }).map((_, j) => (
            <Skeleton key={`c-${i}-${j}`} className="h-6 w-[100px] bg-gray-800/50" />
          ))}
        </div>
      ))}
    </div>
  );
}
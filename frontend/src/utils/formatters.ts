/**
 * Formatting utility functions for NETfix Network Operations Dashboard.
 */

export function formatThroughput(mbps: number | null | undefined): string {
  if (mbps === null || mbps === undefined || isNaN(mbps)) {
    return '—';
  }
  return `${mbps.toFixed(2)} Mbps`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || isNaN(bytes)) {
    return '—';
  }
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

export function formatPackets(count: number | null | undefined): string {
  if (count === null || count === undefined || isNaN(count)) {
    return '—';
  }
  return count.toLocaleString();
}

export function formatPercent(percent: number | null | undefined): string {
  if (percent === null || percent === undefined || isNaN(percent)) {
    return '0.00%';
  }
  return `${percent.toFixed(2)}%`;
}

export function formatIsoTimestamp(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return isoString;
  }
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || isNaN(seconds)) {
    return '—';
  }
  return `${seconds.toFixed(1)}s`;
}

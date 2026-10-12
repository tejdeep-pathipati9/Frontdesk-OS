"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "../ui/button";

export type Column<T> = { key: string; title: string; render: (row: T) => ReactNode; sortValue?: (row: T) => string | number; className?: string };
export function DataTable<T>({ rows, columns, rowKey, onRowClick, empty, pageSize = 10 }: { rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; empty?: ReactNode; pageSize?: number }) {
  const [sort, setSort] = useState<{ key: string; descending: boolean } | null>(null);
  const [page, setPage] = useState(0);
  const count = Math.max(1, Math.ceil(rows.length / pageSize));
  useEffect(() => { setPage(p => Math.min(p, count - 1)); }, [count]);
  const sorted = [...rows];
  const sorting = columns.find(c => c.key === sort?.key);
  if (sorting?.sortValue) sorted.sort((a, b) => { const x = sorting.sortValue!(a), y = sorting.sortValue!(b); const compare = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y)); return sort?.descending ? -compare : compare; });
  const visible = sorted.slice(page * pageSize, (page + 1) * pageSize);
  return <div className="data-table-container"><div className="table-scroll"><table className="data-table" data-slot="table"><thead><tr>{columns.map(c => <th key={c.key} scope="col" className={c.className} aria-sort={sort?.key === c.key ? sort.descending ? "descending" : "ascending" : undefined}>{c.sortValue ? <button onClick={() => { setSort(s => ({ key: c.key, descending: s?.key === c.key ? !s.descending : false })); setPage(0); }}>{c.title}{sort?.key === c.key ? sort.descending ? <ArrowDown size={12} /> : <ArrowUp size={12} /> : <ChevronsUpDown size={12} />}</button> : c.title}</th>)}</tr></thead><tbody>{visible.map(row => <tr key={rowKey(row)} className={onRowClick ? "clickable-row" : undefined} onClick={() => onRowClick?.(row)} onKeyDown={event => { if (onRowClick && event.key === "Enter") onRowClick(row); }} tabIndex={onRowClick ? 0 : undefined}>{columns.map(c => <td key={c.key} className={c.className}>{c.render(row)}</td>)}</tr>)}</tbody></table></div>{!rows.length && empty}{rows.length > pageSize && <div className="table-pagination"><span>{page * pageSize + 1}–{Math.min((page + 1) * pageSize, rows.length)} of {rows.length}</span><div><Button variant="ghost" size="icon" disabled={page === 0} aria-label="Previous page" onClick={() => setPage(p => p - 1)}><ChevronLeft size={14} /></Button><span>Page {page + 1} of {count}</span><Button variant="ghost" size="icon" disabled={page + 1 >= count} aria-label="Next page" onClick={() => setPage(p => p + 1)}><ChevronRight size={14} /></Button></div></div>}</div>;
}

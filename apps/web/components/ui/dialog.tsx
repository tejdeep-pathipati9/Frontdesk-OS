"use client";
// Accessible modal dialog wrapper built on Radix.
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
/** Modal dialog with title, description and optional wide layout. */
export function Dialog({ title, description, children, open, onClose, wide = false }: { title: string; description?: string; children: React.ReactNode; open: boolean; onClose: () => void; wide?: boolean }) {
  return <DialogPrimitive.Root open={open} onOpenChange={value => { if (!value) onClose(); }}><DialogPrimitive.Portal><DialogPrimitive.Overlay className="dialog-overlay" /><DialogPrimitive.Content className={`dialog-content ${wide ? "dialog-wide" : ""}`} aria-describedby={description ? "dialog-description" : undefined}><div className="dialog-heading"><div><DialogPrimitive.Title>{title}</DialogPrimitive.Title>{description && <DialogPrimitive.Description id="dialog-description">{description}</DialogPrimitive.Description>}</div><DialogPrimitive.Close className="icon-button" aria-label="Close dialog"><X size={20} /></DialogPrimitive.Close></div>{children}</DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>;
}

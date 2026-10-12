import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
const variants = cva("button", { variants: { variant: { default: "button-primary", outline: "button-outline", ghost: "button-ghost", destructive: "button-danger" }, size: { default: "", sm: "button-sm", icon: "button-icon" } }, defaultVariants: { variant: "default", size: "default" } });
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof variants> {}
export function Button({ className, variant, size, ...props }: ButtonProps) { return <button className={twMerge(clsx(variants({ variant, size }), className))} {...props} />; }

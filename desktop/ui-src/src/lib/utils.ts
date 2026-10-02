import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 惯例的类名合并(cva + tailwind-merge)。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

import { Clapperboard } from 'lucide-react';
import clsx from 'clsx';

export default function BrandMark({ className = 'h-8 w-8' }: { className?: string }) {
  return <span aria-hidden="true" className={clsx('studio-brand-mark flex shrink-0 items-center justify-center rounded-xl', className)}><Clapperboard size={20} strokeWidth={1.8} /></span>;
}

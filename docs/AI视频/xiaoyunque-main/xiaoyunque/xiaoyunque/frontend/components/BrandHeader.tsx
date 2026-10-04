'use client';

import MediaImage from '@/components/MediaImage';

import Link from 'next/link';

export default function BrandHeader() {
  return (
    <>
      <header className="xyq-brand-header fixed top-0 right-0 left-[var(--app-sidebar-width)] z-30 h-14 bg-white border-b border-gray-200 flex items-center px-4 min-w-0 transition-[left] duration-300">
        <Link href="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity flex-shrink-0">
          <MediaImage
            src="/logo.jpg"
            alt="Logo"
            className="w-8 h-8 rounded-lg object-contain"
          />
          <span className="font-bold text-sm text-gray-800 tracking-tight">
            小云雀 · 创作工作室
          </span>
        </Link>
      </header>
      <div className="h-14 flex-shrink-0" />
    </>
  );
}

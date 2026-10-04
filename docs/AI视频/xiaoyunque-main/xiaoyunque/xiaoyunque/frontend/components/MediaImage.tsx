import Image, { type ImageProps } from 'next/image';

/** Preserve direct media URLs, including local API results, without a server image proxy. */
export default function MediaImage({ width = 1024, height = 1024, alt, ...props }: Omit<ImageProps, 'unoptimized'>) {
  return <Image {...props} width={width} height={height} alt={alt} unoptimized />;
}

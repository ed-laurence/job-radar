import type { ComponentProps } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

/**
 * LLM text is untrusted (CODE-25). Raw HTML is never parsed (react-markdown's default), the
 * resulting tree is sanitized, images are dropped (no tracking pixels or data exfiltration via
 * URLs), and links open with no referrer.
 */
const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((t) => t !== "img"),
  protocols: { ...defaultSchema.protocols, href: ["http", "https", "mailto"] },
};

const SAFE_URL = /^(https?:|mailto:|#|\/(?!\/))/i;

function urlTransform(url: string): string {
  const safe = defaultUrlTransform(url);
  return SAFE_URL.test(safe) ? safe : "";
}

const components: ComponentProps<typeof ReactMarkdown>["components"] = {
  a: ({ node: _node, href, children, ...rest }) =>
    href ? (
      <a {...rest} href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  img: () => null,
};

export interface MarkdownProps {
  children: string;
  className?: string;
}

export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, schema]]}
        urlTransform={urlTransform}
        components={components}
        skipHtml
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

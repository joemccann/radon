const EM_DASH = /(?:\u2014|&mdash;|&#0*8212;|&#x0*2014;)/gi;

/** Last-mile copy policy. Keep source files, minus signs and URL destinations intact. */
export function withoutEmDashes(text: string): string {
  const normalizeCopy = (copy: string) => copy.replace(EM_DASH, "\u2014")
      .replace(/^(\s*)\u2014[ \t]+/gm, "$1- ")
      .replace(/(\d(?:%|bps)?)[ \t]*\u2014[ \t]*(?=[$€£¥]?[+\-−]?\d)/gi, "$1 to ")
      .replace(/[ \t]*\u2014[ \t]*/g, ", ")
      .replace(/([,.;:!?])[ \t]*,[ \t]*/g, "$1 ")
      .replace(/,[ \t]*(?=[,.;:!?])/g, "");
  return text.split(/(https?:\/\/[^\s<>]+)/g).map((part, index) => {
    if (!(index % 2)) return normalizeCopy(part);
    // Parentheses can belong to the path. Only an unmatched closing parenthesis
    // ends a Markdown destination; following prose still needs normalization.
    let depth = 0;
    let end = part.length;
    for (let i = 0; i < part.length; i++) {
      if (part[i] === "(") depth++;
      if (part[i] === ")" && --depth < 0) {
        end = i;
        break;
      }
    }
    return part.slice(0, end).replace(EM_DASH, "%E2%80%94") + normalizeCopy(part.slice(end));
  }).join("");
}

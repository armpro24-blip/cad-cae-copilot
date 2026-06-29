import { useEffect, type RefObject } from "react";

/**
 * Publishes an element's bottom edge (in viewport coordinates) to a CSS custom
 * property on :root, kept in sync via ResizeObserver. Lets absolutely-positioned
 * siblings clear a content-variable element regardless of how it wraps — e.g.
 * the inspector rail staying below the viewer header, which grows taller as the
 * results hero wraps on narrow viewports. Publishing the bottom edge (not the
 * height) keeps it correct even when the rail's containing block is the viewport
 * (so the topbar above the header is already accounted for).
 */
export function useElementBottomVar(ref: RefObject<HTMLElement | null>, varName: string): void {
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => {
      root.style.setProperty(varName, `${Math.round(el.getBoundingClientRect().bottom)}px`);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    window.addEventListener("resize", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      root.style.removeProperty(varName);
    };
  }, [ref, varName]);
}

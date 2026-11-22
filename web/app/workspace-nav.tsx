"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { Activity, Boxes, ChevronDown, ClipboardList, Mail, PackageCheck, Users } from "lucide-react";
import { workspaceLocation } from "./workspace-location";

const primary = [
  { label: "Overview", href: "/" },
  { label: "Risk ratings", href: "/observability?agent=risk" },
] as const;

const more = [
  { label: "Packing forecast", href: "/observability?agent=planner", icon: PackageCheck },
  { label: "Findings", href: "/?view=findings", icon: ClipboardList },
  { label: "Supplier email", href: "/?view=emails", icon: Mail },
  { label: "Agent Team", href: "/observability", icon: Users },
  { label: "Activity", href: "/?view=activity", icon: Activity },
] as const;

export function NavigationBar({ location }: { location?: ReturnType<typeof workspaceLocation> }) {
  const disclosure = useRef<HTMLDetailsElement>(null);
  const toggle = useRef<HTMLElement>(null);

  useEffect(() => {
    const menu = disclosure.current;
    if (!menu) return;
    function closeOutside(event: PointerEvent | FocusEvent) {
      if (event.target instanceof Node && !menu?.contains(event.target)) menu?.removeAttribute("open");
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && menu?.open) {
        menu.open = false;
        toggle.current?.focus();
      }
    }
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("focusin", closeOutside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("focusin", closeOutside);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  function current(label: string) {
    if (location?.section === label) return location.title === label ? "page" : "location";
  }
  const moreActive = more.some(item => item.label === location?.section);

  return (
    <header className="workspace-bar">
      <a href="#workspace-content" className="workspace-skip">Skip to content</a>
      <div className="workspace-bar-inner">
        <Link href="/" className="workspace-brand" aria-label="Line control home">
          <Boxes size={23} strokeWidth={1.65} aria-hidden="true" />
          <span>Line control</span>
        </Link>
        <nav aria-label="Main navigation" className="workspace-main-nav">
          {primary.map(({ href, label }) => <Link key={href} href={href} aria-current={current(label)} className="workspace-nav-item">{label}</Link>)}
          <details ref={disclosure} className="workspace-more">
            <summary ref={toggle} className="workspace-nav-item workspace-menu-toggle" data-current={moreActive || undefined}>
              More <ChevronDown size={15} aria-hidden="true" />
            </summary>
            <ul className="workspace-menu-panel">
              {more.map(({ href, label, icon: Icon }) => <li key={href}>
                <Link href={href} aria-current={current(label)} className="workspace-menu-item"
                  onClick={() => { if (disclosure.current) disclosure.current.open = false; }}>
                  <Icon size={17} aria-hidden="true" />{label}
                </Link>
              </li>)}
            </ul>
          </details>
        </nav>
      </div>
    </header>
  );
}

export default function WorkspaceNav() {
  const path = usePathname();
  const params = useSearchParams();
  return <NavigationBar key={`${path}?${params}`} location={workspaceLocation(path, params.get("view"), params.get("agent"))} />;
}

"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { Activity, Boxes, ClipboardList, LayoutDashboard, Mail, Menu, PackageCheck, Users } from "lucide-react";
import { workspaceLocation } from "./workspace-location";

const destinations = [
  { label: "Overview", href: "/", icon: LayoutDashboard },
  { label: "Risk ratings", href: "/observability?agent=risk", icon: Activity },
  { label: "Packing forecast", href: "/observability?agent=planner", icon: PackageCheck },
  { label: "Findings", href: "/?view=findings", icon: ClipboardList },
  { label: "Supplier email", href: "/?view=emails", icon: Mail },
  { label: "Agent Team", href: "/observability", icon: Users },
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

  const links = (compact: boolean) => destinations.map(({ href, label, icon: Icon }) => {
    const active = location?.section === label;
    return (
      <Link key={href} href={href}
        aria-current={active ? (label === location.title ? "page" : "location") : undefined}
        className={compact ? "workspace-menu-item" : "workspace-nav-item"}
        onClick={compact ? () => { if (disclosure.current) disclosure.current.open = false; } : undefined}>
        {compact && <Icon size={17} aria-hidden="true" />}
        {label}
      </Link>
    );
  });

  return (
    <header className="workspace-bar">
      <a href="#workspace-content" className="workspace-skip">Skip to content</a>
      <div className="workspace-bar-inner">
        <Link href="/" className="workspace-brand" aria-label="Line control home">
          <Boxes size={23} strokeWidth={1.65} aria-hidden="true" />
          Line control
        </Link>
        <nav aria-label="Main navigation" className="workspace-wide-nav">{links(false)}</nav>
        <details ref={disclosure} className="workspace-compact-nav">
          <summary ref={toggle} className="workspace-menu-toggle">
            <Menu size={18} aria-hidden="true" /> Menu
          </summary>
          <nav aria-label="Main navigation" className="workspace-menu-panel">{links(true)}</nav>
        </details>
      </div>
    </header>
  );
}

export default function WorkspaceNav() {
  const path = usePathname();
  const params = useSearchParams();
  return <NavigationBar key={`${path}?${params}`} location={workspaceLocation(path, params.get("view"), params.get("agent"))} />;
}

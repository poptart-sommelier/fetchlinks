import Link from "next/link";

export const dynamic = "force-static";

type AdminSection = {
  href: string;
  title: string;
  description: string;
  status: "available" | "planned";
};

const SECTIONS: AdminSection[] = [
  {
    href: "/admin/feeds",
    title: "RSS feeds",
    description: "Add, search, and remove RSS feed subscriptions.",
    status: "available",
  },
  {
    href: "/admin/reddit",
    title: "Reddit",
    description: "Add, search, and remove subreddit subscriptions.",
    status: "available",
  },
  {
    href: "/admin/bluesky",
    title: "Bluesky",
    description: "Manage handles to follow and credentials. (planned)",
    status: "planned",
  },
  {
    href: "/admin/mastodon",
    title: "Mastodon",
    description: "Manage instances, handles, and credentials. (planned)",
    status: "planned",
  },
];

export default function AdminIndexPage() {
  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/">&larr; Fetchlinks</Link>
          </p>
          <h1>Admin</h1>
        </div>
      </header>

      <section aria-label="Admin sections" className="admin-section-grid">
        {SECTIONS.map((section) => (
          <AdminSectionCard key={section.href} section={section} />
        ))}
      </section>
    </main>
  );
}

function AdminSectionCard({ section }: { section: AdminSection }) {
  const isPlanned = section.status === "planned";
  const className = `admin-section-card${isPlanned ? " admin-section-card-planned" : ""}`;

  if (isPlanned) {
    return (
      <article className={className} aria-disabled="true">
        <h2>{section.title}</h2>
        <p>{section.description}</p>
      </article>
    );
  }

  return (
    <Link className={className} href={section.href}>
      <h2>{section.title}</h2>
      <p>{section.description}</p>
    </Link>
  );
}

/**
 * Landing page with the featured apartment (M10).
 *
 * The page is a doorway: the featured flat's own still fills the top, and
 * clicking it walks in, the way clicking the floor walks in the viewer.
 */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getJson, previewUrl } from '../lib/api';

interface ProjectRow {
  id: string;
  name: string;
  rooms: string;
  preview?: boolean;
}

function roomCount(rooms: string): string {
  return rooms === '1' ? '1 room' : `${rooms} rooms`;
}

export function Landing() {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null);
  const [stillFailed, setStillFailed] = useState(false);

  useEffect(() => {
    getJson<ProjectRow[]>('/api/projects')
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  // A flat with a still is one someone has finished and published.
  const featured = projects?.find((p) => p.preview) ?? projects?.[0];
  const others = projects?.filter((p) => p !== featured) ?? [];
  const showStill = featured?.preview && !stillFailed;

  return (
    <main className="min-h-full pb-14">
      <header className="mx-auto max-w-[90rem] px-5 pb-5 pt-6 sm:px-8">
        <p className="display text-[17px] font-medium">walkthrough</p>
      </header>

      {featured && showStill && (
        // The button below is the accessible way in; this is the same link
        // for a pointer, so it stays out of the tab order.
        <Link
          to={`/p/${featured.id}`}
          tabIndex={-1}
          aria-hidden="true"
          className="doorway mx-auto block max-w-[90rem] px-5 sm:px-8"
        >
          <span className="doorway-frame">
            <img
              src={previewUrl(featured.id)}
              alt=""
              onError={() => setStillFailed(true)}
            />
            <span className="doorway-hint panel">Step inside</span>
          </span>
        </Link>
      )}

      <div className="mx-auto grid max-w-[90rem] gap-x-16 gap-y-10 px-5 pt-12 sm:px-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section>
          <h1 className="display max-w-[15ch] text-balance text-[clamp(2.4rem,5.6vw,4.25rem)] font-medium leading-[1.02] tracking-[-0.025em]">
            Stand inside the flat before you book a viewing.
          </h1>
          <p className="mt-6 max-w-[56ch] text-[16px] leading-relaxed text-ink-soft">
            Real apartments, captured room by room and rebuilt in three dimensions. Walk
            between rooms, look anywhere, watch the light move through the day, and try
            a different floor or style while you stand in them.
          </p>
        </section>

        <aside className="lg:pt-3">
          {featured ? (
            <>
              <p className="display text-[22px] leading-tight">{featured.name}</p>
              <p className="measure mt-1 text-[13px] text-ink-soft">
                {roomCount(featured.rooms)}
              </p>
              <Link
                to={`/p/${featured.id}`}
                className="btn-primary mt-5 inline-flex px-5 py-3.5 text-[15px]"
              >
                Walk through {featured.name}
              </Link>
            </>
          ) : projects === null ? (
            <p className="text-[13px] text-ink-soft">Loading apartments…</p>
          ) : (
            <p className="rule border-l-2 py-1 pl-4 text-[14px] leading-relaxed">
              No apartments are published yet. Generate the synthetic test scene with{' '}
              <code className="measure">pnpm seed:testscene</code>, then start the API
              with <code className="measure">pnpm dev</code>.
            </p>
          )}
        </aside>
      </div>

      {others.length > 0 && (
        <section className="mx-auto mt-20 max-w-[90rem] px-5 sm:px-8">
          <h2 className="rule max-w-[46rem] border-b pb-2 text-[13px] font-semibold">
            Other apartments
          </h2>
          <ul className="max-w-[46rem]">
            {others.map((project) => (
              <li key={project.id} className="rule border-b">
                <Link
                  to={`/p/${project.id}`}
                  className="flex items-baseline justify-between gap-4 py-3.5 hover:opacity-70"
                >
                  <span className="text-[15px]">{project.name}</span>
                  <span className="measure text-[12px] text-ink-soft">
                    {roomCount(project.rooms)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="mx-auto mt-20 max-w-[90rem] px-5 sm:px-8">
        <p className="rule max-w-[62ch] border-t pt-5 text-[12px] leading-relaxed text-ink-soft">
          Floors and furniture shown in a walkthrough are virtual. Any modified view can be
          compared with, or returned to, the original capture at any time.
        </p>
      </footer>
    </main>
  );
}

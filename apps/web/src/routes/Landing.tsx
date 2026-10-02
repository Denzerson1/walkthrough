/** Landing page with the featured apartment (M10). */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getJson } from '../lib/api';

interface ProjectRow {
  id: string;
  name: string;
  rooms: string;
}

export function Landing() {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null);

  useEffect(() => {
    getJson<ProjectRow[]>('/api/projects')
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  const featured = projects?.[0];

  return (
    <main className="mx-auto min-h-full max-w-[72rem] px-5 py-14 sm:px-8">
      <p className="measure text-[12px] opacity-55">walkthrough</p>

      <h1 className="mt-6 max-w-[16ch] text-[clamp(2.4rem,7vw,4.5rem)] font-bold leading-[0.96] tracking-[-0.03em]">
        Stand inside the flat before you book a viewing.
      </h1>

      <p className="mt-6 max-w-[58ch] text-[16px] leading-relaxed opacity-75">
        Real apartments, captured room by room and rebuilt in three dimensions. Walk
        between rooms, look anywhere, and try a different floor or a different style
        while you are standing in them.
      </p>

      {featured ? (
        <Link
          to={`/p/${featured.id}`}
          className="btn-primary mt-10 inline-flex items-baseline gap-4 px-6 py-4"
        >
          <span className="text-[15px] font-semibold">Walk through {featured.name}</span>
          <span className="measure text-[12px] opacity-60">{featured.rooms} rooms</span>
        </Link>
      ) : projects === null ? (
        <p className="mt-10 text-[13px] opacity-55">Loading apartments…</p>
      ) : (
        <div className="rule mt-10 max-w-[60ch] border-l-2 py-1 pl-4">
          <p className="text-[14px] leading-relaxed opacity-80">
            No apartments are published yet. Generate the synthetic test scene with{' '}
            <code className="measure">pnpm seed:testscene</code>, then start the API with{' '}
            <code className="measure">pnpm dev</code>.
          </p>
        </div>
      )}

      {projects && projects.length > 1 && (
        <section className="mt-16">
          <h2 className="rule border-b pb-2 text-[13px] font-semibold tracking-tight">
            Every apartment
          </h2>
          <ul>
            {projects.map((project) => (
              <li key={project.id} className="rule border-b">
                <Link
                  to={`/p/${project.id}`}
                  className="flex items-baseline justify-between gap-4 py-3.5 hover:opacity-70"
                >
                  <span className="text-[15px]">{project.name}</span>
                  <span className="measure text-[12px] opacity-55">
                    {project.rooms} rooms
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="rule mt-20 border-t pt-5">
        <p className="max-w-[62ch] text-[12px] leading-relaxed opacity-50">
          Floors and furniture shown in a walkthrough are virtual. Any modified view is
          marked and can be returned to the original capture at any time.
        </p>
      </footer>
    </main>
  );
}

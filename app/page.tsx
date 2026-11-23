import { ApiStatus } from "./api-status";

const highlights = [
  { label: "Teams", value: "One shared view", detail: "Keep people and schedules connected." },
  { label: "Coverage", value: "Plan with clarity", detail: "See shifts and assignments together." },
  { label: "Platform", value: "Ready to grow", detail: "Built on a typed web and API foundation." },
];

export default function Home() {
  return (
    <main className="shell">
      <header className="topbar"><a className="brand" href="#home"><span className="brand-mark">d</span> datastraw <span className="brand-light">/ roster</span></a><ApiStatus /></header>
      <section className="hero" id="home">
        <p className="eyebrow">SMART ROSTER / TEAM OPERATIONS</p>
        <h1>Good schedules<br /><em>make room</em> for great work.</h1>
        <p className="intro">A calmer way to bring your people, shifts, and coverage into one place. Your roster workspace is ready for its next step.</p>
        <a className="button" href="#overview">Explore the workspace <span aria-hidden="true">&rarr;</span></a>
        <div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orb orb-a" /><div className="orb orb-b" />
      </section>
      <section className="overview" id="overview"><div className="section-heading"><div><p className="eyebrow">A STRONG START</p><h2>Your team, in sync.</h2></div><span className="section-note">The foundation for a smoother day.</span></div>
        <div className="cards">{highlights.map((item, index) => <article className="card" key={item.label}><span className="card-number">0{index + 1}</span><p className="card-label">{item.label}</p><h3>{item.value}</h3><p className="card-detail">{item.detail}</p><span className="card-arrow" aria-hidden="true">&rarr;</span></article>)}</div>
      </section>
      <footer><span>DAT A STRAW <b>·</b> SMART ROSTER</span><span>Thoughtful planning starts here.</span></footer>
    </main>
  );
}

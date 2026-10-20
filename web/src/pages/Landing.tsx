import { Link } from 'react-router-dom'
import { BrandMark } from '../components/AppShell'
import { TableRing } from '../components/TableRing'
import './Landing.css'

// The public landing page is a static site (see web/landing). The app only runs on the person's own
// machine, so there the main button leads to the walkthrough, and the nav and closing buttons are left out.
function Cta({ staticSite, className }: { staticSite: boolean; className: string }) {
  return staticSite
    ? <a href="#see" className={className}>Watch the walkthrough</a>
    : <Link to="/" className={className}>Start a session</Link>
}

const VIDEOS = [
  { file: 'debate.mp4', poster: 'debate-poster.webp', title: 'A debate', caption: 'Three agents argue a release-cadence question, you add a point, and the moderator ends with a decision.', fallback: 'A recording of a Bayto debate: creating a task, seating three agents, watching them argue and reading the synthesis.' },
  { file: 'build.mp4', poster: 'build-poster.webp', title: 'A build', caption: 'The architect asks you a question, a developer, tester and reviewer work through two fix cycles, and you get a branch to merge.', fallback: 'A recording of a Bayto build: an architect, developer, tester and reviewer change code on a new git branch.' },
]

const SPECIALISTS = [
  'Architect', 'Security Reviewer', 'PM', 'Lawyer', 'Customer', 'Historian', 'Developer',
  'QA Tester', 'E2E Tester', 'UX Designer', 'Researcher', 'CFO', 'Technical Writer',
]

const SHOTS = [
  { file: 'setup.webp', title: 'Seat your agents', alt: 'Session setup: a mode, three agents in the roster and a spending cap.' },
  { file: 'synthesis.webp', title: 'Leave with a decision', alt: 'The final synthesis: a decision, key points, next steps and a minority report.' },
  { file: 'build.webp', title: 'Or leave with a change', alt: 'Build mode: a reviewed git branch with test results and diff statistics, ready to merge.' },
  { file: 'board.webp', title: 'Every task in one place', alt: 'The task board listing finished tasks with their results.' },
]
const asset = (name: string) => `${import.meta.env.BASE_URL}walkthrough/${name}`

const STEPS = [
  { title: 'Put the question on the table', body: 'Write a title and a brief, and say what you want back: a decision, a plan, a list of options.' },
  { title: 'Seat your agents', body: 'Choose a mode and a roster. Every agent has a role and a stance, such as pragmatist, adversarial or risk-averse.' },
  { title: 'Watch, steer, decide', body: 'Replies stream in live. Jump in whenever you like, then end the session to get a written synthesis with a minority report.' },
]

const MODES = [
  { name: 'Open chat', tag: 'Raise a hand', tone: 'indigo', body: 'Agents ask for the floor when they have something to add, so the conversation follows the argument instead of a fixed order.' },
  { name: 'Debate', tag: 'Opposing stances', tone: 'rose', body: 'Agents argue from opposite positions in turn while the moderator keeps it fair and stops it when nobody is adding new arguments.' },
  { name: 'Brainstorm', tag: 'Diverge first', tone: 'saffron', body: 'Many ideas, little judgement. Use it when you need options on the table before anyone starts picking holes.' },
  { name: 'Build', tag: 'Plan, code, test, review', tone: 'indigo', body: 'Turn the decision into a change. An architect, developer, tester and reviewer work on a new git branch in your repository. You get the branch, the test results and the review, and you merge it.' },
]

const FEATURES = [
  { title: 'A moderator that keeps score', body: 'It summarises each round and ends the session when the group stops adding new arguments or reaches its round limit.' },
  { title: 'A hard spending cap', body: 'Set a dollar cap before you start. The token budget is a guide, and the cap is the hard stop.' },
  { title: 'Live, interruptible discussion', body: 'Watch replies stream in, pause the table, add your own view to everyone or to one agent, then resume.' },
  { title: 'A minority report', body: 'The synthesis gives you the decision and the strongest dissent, with the turns each point came from.' },
  { title: 'Agents with roles and stances', body: 'Pick from a catalogue such as Architect, Security Reviewer, PM and Lawyer, and order them as you want them to speak.' },
  { title: 'Every session is kept', body: 'Reopen any task to see its transcript, the summaries and the final synthesis.' },
]

const DEMO_SEATS = [
  { key: 'a', initials: 'AR', hue: 232, active: true },
  { key: 's', initials: 'SR', hue: 172 },
  { key: 'p', initials: 'PM', hue: 348 },
]

export function Landing({ staticSite = false }: { staticSite?: boolean } = {}) {
  return (
    <div className="landing" id="top">
      <header className="ld-header">
      <div className="ld-nav">
        {staticSite ? (
          <a href="#top" className="brand" aria-label="Bayto home">
            <BrandMark size={32} />
            Bayto
          </a>
        ) : (
          <Link to="/welcome" className="brand" aria-label="Bayto home">
            <BrandMark size={32} />
            Bayto
          </Link>
        )}
        <nav aria-label="Main" className="ld-links">
          <a href="#how">How it works</a>
          <a href="#modes">Modes</a>
          <a href="#agents">Agents</a>
          <a href="#see">See it</a>
          <a href="#local">Runs locally</a>
          {!staticSite && <Cta staticSite={staticSite} className="btn primary" />}
        </nav>
      </div>
      </header>

      <main>
        <section className="ld-hero">
          <div className="ld-hero-copy">
            <p className="ld-badge"><span>Bayto</span> Debate it. Decide it. Build it.</p>
            <h1>Convene a team of AI agents.</h1>
            <p className="ld-lede">Put a hard question to a table of Claude agents that hold different positions. They debate it live, a moderator keeps it fair, you steer. You leave with a decision and the strongest dissent.</p>
            <div className="ld-cta">
              <Cta staticSite={staticSite} className="btn primary lg" />
              <a href="#how" className="btn lg">See how it works</a>
            </div>
            <p className="ld-fine">Runs on your own Claude subscription. No API key to manage.</p>
          </div>

          <div className="ld-demo card">
            <div className="ld-demo-head">
              <div>
                <div className="ld-demo-kicker">Debate · Round 2</div>
                <div className="ld-demo-title">Weekly or biweekly releases?</div>
              </div>
              <span className="chip ok"><span className="dot" />Live</span>
            </div>
            <div className="ld-demo-body">
              <TableRing size={220} seats={DEMO_SEATS}>The question at the centre</TableRing>
              <div className="ld-demo-msgs">
                <p><b style={{ ['--agent-h' as string]: 232 }}>Architect</b> <span>speaking</span><br />Weekly keeps each release small, so a rollback is cheap.</p>
                <p><b style={{ ['--agent-h' as string]: 172 }}>Security Reviewer</b><br />Small does not mean safe. Who reviews a Friday release?</p>
              </div>
            </div>
          </div>
        </section>

        <section id="how" className="ld-section">
          <p className="eyebrow">How it works</p>
          <h2>From a question to a decision in three moves.</h2>
          <div className="ld-grid">
            {STEPS.map((s, i) => (
              <div key={s.title} className="ld-step card">
                <span className={`ld-num${i === 2 ? ' hot' : ''}`}>{i + 1}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="modes" className="ld-section">
          <p className="eyebrow">Modes</p>
          <h2>Four ways to convene the table.</h2>
          <div className="ld-grid">
            {MODES.map((m) => (
              <div key={m.name} className={`ld-mode ${m.tone}`}>
                <h3>{m.name}</h3>
                <div className="ld-mode-tag">{m.tag}</div>
                <p>{m.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="ld-section">
          <p className="eyebrow">Built for real decisions</p>
          <h2>You stay in charge of the table.</h2>
          <div className="ld-features">
            {FEATURES.map((f) => (
              <div key={f.title} className="ld-feature">
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="agents" className="ld-section">
          <p className="eyebrow">Specialist agents</p>
          <h2>The right expert for the question.</h2>
          <p className="ld-lede">Every agent has a role, a stance and its own instructions. Seat the ones your question needs, and order them as you want them to speak.</p>
          <div className="ld-traits">
            <div>
              <h3>Different roles</h3>
              <p>An architect weighs structure, a lawyer weighs risk, a customer speaks for the user. Each one argues from its own seat, so you hear the disagreement instead of one blended answer.</p>
            </div>
            <div>
              <h3>Different stances</h3>
              <p>Pragmatist, adversarial, risk-averse, cost-conscious. The stance sets how an agent pushes back, so a security reviewer attacks the plan while a PM defends the scope.</p>
            </div>
            <div>
              <h3>Different permissions</h3>
              <p>In a discussion, agents only talk: no files, no shell. In Build, each role gets only what its job needs: the architect plans without editing, the developer edits and commits, the tester and reviewer only read. No agent is allowed to push or use the network.</p>
            </div>
          </div>
          <ul className="ld-roles" aria-label="Agents in the catalogue">
            {SPECIALISTS.map((name) => <li key={name}>{name}</li>)}
          </ul>
          <div className="ld-custom">
            <div>
              <h3>Need a specialist we don't have?</h3>
              <p>Add your own: give it a role, a stance and instructions, such as a compliance officer, a data engineer or a customer from your own market. It joins the catalogue, and you seat it like any other agent.</p>
            </div>
            <span className="chip accent">Custom agents: coming soon</span>
          </div>
        </section>

        <section id="see" className="ld-section">
          <p className="eyebrow">See it in action</p>
          <h2>Two sessions, start to finish.</h2>
          <div className="ld-videos">
            {VIDEOS.map((v) => (
              <figure key={v.file}>
                <video className="ld-video" controls muted playsInline preload="none" poster={asset(v.poster)} aria-label={v.title}>
                  <source src={asset(v.file)} type="video/mp4" />
                  {v.fallback}
                </video>
                <figcaption><strong>{v.title}</strong> {v.caption}</figcaption>
              </figure>
            ))}
          </div>
          <p className="ld-fine">Both recordings use sample agents.</p>
          <div className="ld-shots">
            {SHOTS.map((s) => (
              <figure key={s.file}>
                <img src={asset(s.file)} alt={s.alt} loading="lazy" width="1280" height="800" />
                <figcaption>{s.title}</figcaption>
              </figure>
            ))}
          </div>
        </section>

        <section id="local" className="ld-section">
          <div className="ld-band">
            <div>
              <h2>Your machine. Your Claude subscription.</h2>
              <p>Agents work in local Docker sandboxes on your own computer and the moderator signs in with your own Claude login. There is no API key to create, store or leak.</p>
            </div>
            <ul>
              <li>Local Docker sandboxes</li>
              <li>Claude subscription, no API key</li>
              <li>Dollar cap as the hard stop</li>
            </ul>
          </div>
        </section>

        <section className="ld-final">
          <h2>Bring the problem. Bayto brings the team.</h2>
          {!staticSite && <Cta staticSite={staticSite} className="btn primary lg" />}
        </section>
      </main>

      <footer className="ld-footer">
        <span>Bayto · a team of AI agents</span>
      </footer>
    </div>
  )
}

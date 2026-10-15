import { Link } from 'react-router-dom'
import { BrandMark } from '../components/AppShell'
import { TableRing } from '../components/TableRing'
import './Landing.css'

const STEPS = [
  { title: 'Put the question on the table', body: 'Write a title and a brief, and say what you want back: a decision, a plan, a list of options.' },
  { title: 'Seat your agents', body: 'Choose a mode and a roster. Every agent has a role and a stance, such as pragmatist, adversarial or risk-averse.' },
  { title: 'Watch, steer, decide', body: 'Replies stream in live. Jump in whenever you like, then end the session to get a written synthesis with a minority report.' },
]

const MODES = [
  { name: 'Open chat', tag: 'Raise a hand', tone: 'indigo', body: 'Agents ask for the floor when they have something to add, so the conversation follows the argument instead of a fixed order.' },
  { name: 'Debate', tag: 'Opposing stances', tone: 'rose', body: 'Agents argue from opposite positions in turn while the moderator keeps it fair and stops it when nobody is adding new arguments.' },
  { name: 'Brainstorm', tag: 'Diverge first', tone: 'saffron', body: 'Many ideas, little judgement. Use it when you need options on the table before anyone starts picking holes.' },
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

export function Landing() {
  return (
    <div className="landing">
      <header className="ld-nav">
        <Link to="/welcome" className="brand" aria-label="Bayto home">
          <BrandMark size={32} />
          Bayto
          <span className="brand-ge" lang="ti" aria-hidden="true">ባይቶ</span>
        </Link>
        <nav aria-label="Main" className="ld-links">
          <a href="#how">How it works</a>
          <a href="#modes">Modes</a>
          <a href="#local">Runs locally</a>
          <Link to="/" className="btn primary">Start a session</Link>
        </nav>
      </header>

      <main>
        <section className="ld-hero">
          <div className="ld-hero-copy">
            <p className="ld-badge"><span lang="ti">ባይቶ</span> an assembly, a council</p>
            <h1>Convene a council of AI agents.</h1>
            <p className="ld-lede">Put a hard question to a table of Claude agents that hold different positions. They debate it live, a moderator keeps it fair, you steer. You leave with a decision and the strongest dissent.</p>
            <div className="ld-cta">
              <Link to="/" className="btn primary lg">Start a session</Link>
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
          <h2>Three ways to convene the table.</h2>
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
          <h2>Bring the question. Bayto brings the table.</h2>
          <Link to="/" className="btn primary lg">Start a session</Link>
        </section>
      </main>

      <footer className="ld-footer">
        <span>Bayto <span lang="ti">ባይቶ</span> · a council of AI agents</span>
      </footer>
    </div>
  )
}

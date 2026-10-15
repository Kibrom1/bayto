import { NavLink, Outlet } from 'react-router-dom'

export function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="shell-header">
        <NavLink to="/" className="brand" aria-label="Bayto home">
          <span className="brand-mark" aria-hidden="true">ባ</span>
          Bayto
        </NavLink>
        <nav className="shell-nav" aria-label="Main">
          <NavLink to="/" end>Tasks</NavLink>
          <NavLink to="/services">Services</NavLink>
        </nav>
      </header>
      <div id="main">
        <Outlet />
      </div>
    </>
  )
}

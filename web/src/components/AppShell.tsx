import { NavLink, Outlet } from 'react-router-dom'

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="16" cy="4" r="3.2" fill="currentColor" />
      <circle cx="26.4" cy="22" r="3.2" fill="currentColor" />
      <circle cx="5.6" cy="22" r="3.2" fill="#f5b301" />
      <circle cx="16" cy="16" r="2" fill="currentColor" />
    </svg>
  )
}

export function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="shell-header">
        <NavLink to="/" className="brand" aria-label="Bayto home">
          <BrandMark />
          Bayto
          <span className="brand-ge" lang="ti" aria-hidden="true">ባይቶ</span>
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

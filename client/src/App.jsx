import React, { useState, useEffect, useRef } from 'react'
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom'
import { loadStripe } from '@stripe/stripe-js'
import { fetchConfig } from './utils/api'
import ProductsPage from './pages/ProductsPage'
import CheckoutPage from './pages/CheckoutPage'
import ConfirmationPage from './pages/ConfirmationPage'
import FraudDashboard from './pages/FraudDashboard'
import ToolDashboard from './pages/ToolDashboard'
import UsageDashboard from './pages/UsageDashboard'

// The shared .page wrapper caps width at 960px for the storefront pages
// (product grid, checkout). Wide dashboard-style pages (sidebar + tables)
// need more room, so they opt into a wider max-width here.
const WIDE_PAGE_ROUTES = ['/tools']

// The locale/currency indicator bar is only meaningful on the storefront
// demo pages — Fraud Lab / Tools / Usage don't use locale or currency,
// so it doesn't belong there.
const STOREFRONT_ROUTES = ['/', '/checkout', '/confirmation']

function PageWrapper({ locale, currency, children }) {
  const location = useLocation()
  const isWide = WIDE_PAGE_ROUTES.includes(location.pathname)
  const showLocaleBar = STOREFRONT_ROUTES.includes(location.pathname)
  return (
    <div className="page" style={isWide ? { maxWidth: 1400 } : undefined}>
      {showLocaleBar && (
        <div className="locale-bar">
          <span className="dot" />
          <span>locale: {locale}</span>
          <span>|</span>
          <span>currency: {currency.toUpperCase()}</span>
          <span>|</span>
          <span>browser: {navigator.language}</span>
        </div>
      )}
      {children}
    </div>
  )
}

const navLinkStyle = {
  textDecoration: 'none',
  color: 'var(--ink-muted)',
  fontSize: '0.9rem',
  fontWeight: 500,
}

const selectStyle = {
  padding: '0.35rem 0.5rem',
  fontSize: '0.82rem',
  fontFamily: 'var(--font-mono)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  background: 'transparent',
}

// Groups the storefront-demo controls (locale, currency, product grid, cart)
// under one menu instead of sitting as top-level nav items next to the
// app's actual capabilities (Fraud Lab / Tools / Usage).
function MerchantDemoMenu({ locale, setLocale, currency, setCurrency, currencies, cartCount }) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.35rem',
          background: 'transparent', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)', padding: '0.35rem 0.75rem',
          fontSize: '0.9rem', fontWeight: 500, color: 'var(--ink-secondary)',
          cursor: 'pointer', fontFamily: 'inherit',
        }}
      >
        Merchant Demo {cartCount > 0 && <span className="badge badge-success">{cartCount}</span>} {open ? '▴' : '▾'}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 0.5rem)', right: 0,
          background: 'var(--surface-card)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-md)',
          padding: '0.75rem', minWidth: 200, display: 'flex', flexDirection: 'column', gap: '0.6rem',
          zIndex: 10,
        }}>
          <Link to="/" onClick={() => setOpen(false)} style={navLinkStyle}>Product Grid</Link>
          <Link to="/checkout" onClick={() => setOpen(false)} style={{ ...navLinkStyle, display: 'flex', alignItems: 'center', gap: '0.35rem', color: cartCount > 0 ? 'var(--accent)' : 'var(--ink-muted)' }}>
            Cart {cartCount > 0 && <span className="badge badge-success">{cartCount}</span>}
          </Link>

          <div style={{ borderTop: '1px solid var(--border)', margin: '0.1rem 0' }} />

          <label style={{ fontSize: '0.72rem', color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '-0.35rem' }}>
            Locale
          </label>
          <select value={locale} onChange={(e) => setLocale(e.target.value)} style={selectStyle}>
            <option value="en-US">en-US</option>
            <option value="en-CA">en-CA</option>
            <option value="fr-CA">fr-CA</option>
            <option value="fr-FR">fr-FR</option>
            <option value="es-ES">es-ES</option>
            <option value="es-MX">es-MX</option>
            <option value="de-DE">de-DE</option>
            <option value="ja-JP">ja-JP</option>
            <option value="pt-BR">pt-BR</option>
            <option value="zh-CN">zh-CN</option>
            <option value="hi-IN">hi-IN</option>
            <option value="fi-FI">fi-FI</option>
            <option value="ko-KR">ko-KR</option>
          </select>

          <label style={{ fontSize: '0.72rem', color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '-0.35rem' }}>
            Currency
          </label>
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} style={selectStyle}>
            {currencies.map((c) => (
              <option key={c} value={c}>{c.toUpperCase()}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [stripePromise, setStripePromise] = useState(null)
  const [config, setConfig] = useState(null)
  const [cart, setCart] = useState([])
  const [locale, setLocale] = useState(navigator.language || 'en-US')
  const [currency, setCurrency] = useState('usd')

  useEffect(() => {
    fetchConfig().then(cfg => {
      setConfig(cfg)
      setStripePromise(loadStripe(cfg.stripe_publishable_key))
    }).catch(err => {
      console.error('Failed to load config:', err)
    })
  }, [])

  const addToCart = (product) => {
    setCart(prev => {
      const existing = prev.find(item => item.id === product.id)
      if (existing) {
        return prev.map(item =>
          item.id === product.id
            ? { ...item, quantity: item.quantity + 1 }
            : item
        )
      }
      return [...prev, { id: product.id, name: product.name, price: product.price, quantity: 1 }]
    })
  }

  const removeFromCart = (productId) => {
    setCart(prev => prev.filter(item => item.id !== productId))
  }

  const clearCart = () => setCart([])

  const cartTotal = cart.reduce((sum, item) => sum + item.price * item.quantity, 0)
  const cartCount = cart.reduce((sum, item) => sum + item.quantity, 0)

  if (!config || !stripePromise) {
    return (
      <div className="page" style={{ textAlign: 'center', paddingTop: '4rem' }}>
        <div className="spinner" style={{ margin: '0 auto', borderColor: 'rgba(0,0,0,0.15)', borderTopColor: 'var(--accent)' }} />
        <p style={{ marginTop: '1rem', color: 'var(--ink-muted)' }}>Connecting to payment server...</p>
      </div>
    )
  }

  return (
    <BrowserRouter>
      <nav style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0.75rem 1.5rem',
        borderBottom: '1px solid var(--border)',
        background: 'var(--surface-card)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '2rem' }}>
          <Link to="/" style={{ textDecoration: 'none', color: 'var(--ink)', fontWeight: 500, fontSize: '1.1rem', letterSpacing: '-0.01em' }}>
            <span style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', fontSize: '0.85rem', marginRight: '0.5rem' }}>&#9632;</span>
            Payment Lab
          </Link>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
            <Link to="/fraud" style={navLinkStyle}>Fraud Lab</Link>
            <Link to="/tools" style={navLinkStyle}>Tools</Link>
            <Link to="/metering" style={navLinkStyle}>Usage</Link>
          </div>
        </div>

        <MerchantDemoMenu
          locale={locale}
          setLocale={setLocale}
          currency={currency}
          setCurrency={setCurrency}
          currencies={config.supported_currencies}
          cartCount={cartCount}
        />
      </nav>

      <PageWrapper locale={locale} currency={currency}>
        <Routes>
          <Route path="/" element={
            <ProductsPage
              addToCart={addToCart}
              cart={cart}
              currency={currency}
              locale={locale}
            />
          } />
          <Route path="/checkout" element={
            <CheckoutPage
              stripePromise={stripePromise}
              cart={cart}
              cartTotal={cartTotal}
              currency={currency}
              locale={locale}
              removeFromCart={removeFromCart}
              clearCart={clearCart}
            />
          } />
          <Route path="/confirmation" element={
            <ConfirmationPage locale={locale} currency={currency} />
          } />
          <Route path="/fraud" element={
            <FraudDashboard />
          } />
          <Route path="/tools" element={
            <ToolDashboard />
          } />
          <Route path="/metering" element={
            <UsageDashboard />
          } />
        </Routes>
      </PageWrapper>
    </BrowserRouter>
  )
}

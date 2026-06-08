import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import { loadStripe } from '@stripe/stripe-js'
import { fetchConfig } from './utils/api'
import ProductsPage from './pages/ProductsPage'
import CheckoutPage from './pages/CheckoutPage'
import ConfirmationPage from './pages/ConfirmationPage'
import FraudDashboard from './pages/FraudDashboard'
import ToolDashboard from './pages/ToolDashboard'

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
        <Link to="/" style={{ textDecoration: 'none', color: 'var(--ink)', fontWeight: 500, fontSize: '1.1rem', letterSpacing: '-0.01em' }}>
          <span style={{ color: 'var(--accent)', fontFamily: 'var(--font-mono)', fontSize: '0.85rem', marginRight: '0.5rem' }}>&#9632;</span>
          Payment Lab
        </Link>

        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {/* Locale selector */}
          <select
            value={locale}
            onChange={e => setLocale(e.target.value)}
            style={{ padding: '0.35rem 0.5rem', fontSize: '0.82rem', fontFamily: 'var(--font-mono)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'transparent' }}
          >
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

          {/* Currency selector */}
          <select
            value={currency}
            onChange={e => setCurrency(e.target.value)}
            style={{ padding: '0.35rem 0.5rem', fontSize: '0.82rem', fontFamily: 'var(--font-mono)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'transparent' }}
          >
            {config.supported_currencies.map(c => (
              <option key={c} value={c}>{c.toUpperCase()}</option>
            ))}
          </select>

          {/* Cart indicator */}
          <Link to="/fraud" style={{
            textDecoration: 'none',
            color: 'var(--ink-muted)',
            fontSize: '0.9rem',
            fontWeight: 500,
          }}>
            Fraud Lab
          </Link>

          <Link to="/tools" style={{
            textDecoration: 'none',
            color: 'var(--ink-muted)',
            fontSize: '0.9rem',
            fontWeight: 500,
          }}>
            Tools
          </Link>

          <Link to="/checkout" style={{
            textDecoration: 'none',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
            color: cartCount > 0 ? 'var(--accent)' : 'var(--ink-muted)',
            fontSize: '0.9rem',
            fontWeight: 500,
          }}>
            Cart {cartCount > 0 && <span className="badge badge-success">{cartCount}</span>}
          </Link>
        </div>
      </nav>

      {/* Locale indicator bar */}
      <div className="page">
        <div className="locale-bar">
          <span className="dot" />
          <span>locale: {locale}</span>
          <span>|</span>
          <span>currency: {currency.toUpperCase()}</span>
          <span>|</span>
          <span>browser: {navigator.language}</span>
        </div>

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
        </Routes>
      </div>
    </BrowserRouter>
  )
}

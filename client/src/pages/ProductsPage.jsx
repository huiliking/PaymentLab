import React, { useState, useEffect } from 'react'
import { fetchProducts } from '../utils/api'
import { formatPrice, convertPrice } from '../utils/currency'

export default function ProductsPage({ addToCart, cart, currency, locale }) {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchProducts()
      .then(data => setProducts(data.products))
      .catch(err => console.error(err))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <p style={{ color: 'var(--ink-muted)' }}>Loading products...</p>
  }

  const inCart = (id) => cart.find(item => item.id === id)

  return (
    <div>
      <div className="page-header">
        <h1>Products</h1>
        <p>Pick something to test the checkout flow</p>
      </div>

      <div className="product-grid">
        {products.map((product, i) => (
          <div
            key={product.id}
            className="card animate-in"
            style={{
              animationDelay: `${i * 0.08}s`,
              display: 'flex',
              flexDirection: 'column',
              gap: '0.75rem',
            }}
          >
            {/* Product icon placeholder */}
            <div style={{
              width: '100%',
              height: '120px',
              background: 'var(--surface-elevated)',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '2rem',
              color: 'var(--ink-muted)',
            }}>
              {product.id === 'prod_001' ? '🧰' : product.id === 'prod_002' ? '⚡' : '☁️'}
            </div>

            <div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: 500 }}>{product.name}</h3>
              <p style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)', marginTop: '0.2rem' }}>
                {product.description}
              </p>
            </div>

            <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '1.15rem', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                {formatPrice(convertPrice(product.price, currency), currency, locale)}
              </span>

              <button
                className={inCart(product.id) ? 'btn btn-ghost' : 'btn btn-primary'}
                onClick={() => addToCart(product)}
                style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
              >
                {inCart(product.id) ? `In cart (${inCart(product.id).quantity})` : 'Add to cart'}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Localization note */}
      <div style={{
        marginTop: '2rem',
        padding: '1rem',
        background: 'var(--accent-soft)',
        borderRadius: 'var(--radius-md)',
        fontSize: '0.82rem',
        color: 'var(--accent)',
        fontFamily: 'var(--font-mono)',
        lineHeight: 1.7,
      }}>
        <strong>Localization surface:</strong> Try switching locale and currency in the nav bar. 
        Watch how {formatPrice(4999, 'usd', 'en-US')} becomes {formatPrice(convertPrice(4999, 'eur'), 'eur', 'de-DE')} or {formatPrice(convertPrice(4999, 'jpy'), 'jpy', 'ja-JP')}.
        <br />
        Current: {formatPrice(convertPrice(products[0]?.price || 0, currency), currency, locale)}
      </div>
    </div>
  )
}

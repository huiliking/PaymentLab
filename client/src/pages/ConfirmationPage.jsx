import React, { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { checkPaymentStatus } from '../utils/api'
import { formatPrice } from '../utils/currency'

export default function ConfirmationPage({ locale, currency }) {
  const [searchParams] = useSearchParams()
  const paymentIntentId = searchParams.get('payment_intent')
  const orderId = searchParams.get('order')

  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (paymentIntentId) {
      checkPaymentStatus(paymentIntentId)
        .then(data => setStatus(data))
        .catch(err => console.error(err))
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [paymentIntentId])

  if (!paymentIntentId) {
    return (
      <div>
        <div className="page-header">
          <h1>No payment found</h1>
        </div>
        <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
          <p style={{ color: 'var(--ink-muted)', marginBottom: '1rem' }}>
            No payment information in the URL.
          </p>
          <Link to="/" className="btn btn-primary">Go to products</Link>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ textAlign: 'center', paddingTop: '3rem' }}>
        <div className="spinner" style={{ margin: '0 auto', borderColor: 'rgba(0,0,0,0.15)', borderTopColor: 'var(--accent)' }} />
        <p style={{ marginTop: '1rem', color: 'var(--ink-muted)' }}>Checking payment status...</p>
      </div>
    )
  }

  const succeeded = status?.status === 'succeeded'

  return (
    <div>
      <div className="page-header">
        <h1>{succeeded ? 'Payment confirmed' : 'Payment status'}</h1>
      </div>

      <div className="card animate-in" style={{ maxWidth: '520px' }}>
        {/* Status indicator */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          marginBottom: '1.5rem',
        }}>
          <div style={{
            width: '48px', height: '48px', borderRadius: '50%',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '1.4rem',
            background: succeeded ? 'var(--success-soft)' : 'var(--warning-soft)',
          }}>
            {succeeded ? '✓' : '⏳'}
          </div>
          <div>
            <span className={`badge ${succeeded ? 'badge-success' : 'badge-warning'}`}>
              {status?.status || 'unknown'}
            </span>
          </div>
        </div>

        {/* Details */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '1rem',
          padding: '1rem',
          background: 'var(--surface-elevated)',
          borderRadius: 'var(--radius-md)',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.82rem',
        }}>
          <div>
            <p style={{ color: 'var(--ink-muted)', marginBottom: '0.2rem' }}>Order</p>
            <p>#{orderId}</p>
          </div>
          <div>
            <p style={{ color: 'var(--ink-muted)', marginBottom: '0.2rem' }}>Amount</p>
            <p>{status ? formatPrice(status.amount, status.currency, locale) : '—'}</p>
          </div>
          <div>
            <p style={{ color: 'var(--ink-muted)', marginBottom: '0.2rem' }}>Currency</p>
            <p>{status?.currency?.toUpperCase() || '—'}</p>
          </div>
          <div>
            <p style={{ color: 'var(--ink-muted)', marginBottom: '0.2rem' }}>Locale</p>
            <p>{locale}</p>
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <p style={{ color: 'var(--ink-muted)', marginBottom: '0.2rem' }}>Payment intent</p>
            <p style={{ wordBreak: 'break-all', fontSize: '0.75rem' }}>{paymentIntentId}</p>
          </div>
        </div>

        {/* Localization note */}
        <div style={{
          marginTop: '1.25rem',
          padding: '0.75rem',
          background: 'var(--accent-soft)',
          borderRadius: 'var(--radius-sm)',
          fontSize: '0.78rem',
          color: 'var(--accent)',
          fontFamily: 'var(--font-mono)',
          lineHeight: 1.7,
        }}>
          <strong>Localization observation:</strong> This confirmation page
          is in English regardless of the selected locale. In production,
          the success message, status labels, and receipt would all need
          translation. This is a gap we'll address in Phase 2.
        </div>

        <div style={{ marginTop: '1.5rem' }}>
          <Link to="/" className="btn btn-primary">Back to products</Link>
        </div>
      </div>
    </div>
  )
}

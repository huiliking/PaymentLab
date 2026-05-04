/**
 * AddressForm.jsx — AI-driven locale-aware address form
 *
 * Instead of hardcoding address structures per country, this component
 * calls a local Ollama LLM to generate the correct field schema for any
 * country/locale. The LLM acts as an agent that knows address conventions
 * worldwide (referencing standards like Google's libaddressinput).
 *
 * Flow:
 *   1. User selects locale (e.g. fr-FR) → extract country code (FR)
 *   2. Call Ollama → "Generate the address form schema for France"
 *   3. LLM returns JSON: field order, labels, placeholders, layout, Stripe mapping
 *   4. Render form dynamically from the schema
 *   5. Cache result so the same country doesn't re-trigger the LLM
 *
 * The onChange callback always emits a normalized Stripe-compatible object:
 *   { name, line1, line2, city, state, postalCode, country }
 *
 * Usage:
 *   <AddressForm locale="fr-FR" onChange={(addr) => console.log(addr)} />
 */

import React, { useState, useEffect, useCallback, useRef } from 'react'

// ─── Schema cache (persists across re-renders, shared by all instances) ───────
const schemaCache = {}

// Expose cache clear for debugging: call window.clearAddressCache() in console
if (typeof window !== 'undefined') {
  window.clearAddressCache = () => {
    Object.keys(schemaCache).forEach(k => delete schemaCache[k])
    console.log('[AddressForm] Cache cleared — reload page to re-fetch')
  }
}

// ─── Country code extraction from locale ─────────────────────────────────────
function getCountryCode(locale) {
  // "fr-FR" → "FR", "ja-JP" → "JP", "en-US" → "US", "es-MX" → "MX"
  const parts = locale.split('-')
  if (parts.length >= 2) return parts[parts.length - 1].toUpperCase()
  return locale.toUpperCase()
}

function getLanguageCode(locale) {
  return locale.split('-')[0].toLowerCase()
}

// ─── Ollama API call ─────────────────────────────────────────────────────────
// ─── Configuration ───────────────────────────────────────────────────────────
// Change OLLAMA_MODEL if llama3.2 is too slow on your hardware.
// Try: "llama3.2:1b" (fastest), "llama3.2" (default 3B), "mistral", "phi3"
// Run `ollama list` in your terminal to see what you have installed.
const OLLAMA_MODEL = 'llama3.2:1b'
const OLLAMA_TIMEOUT_MS = 60000 // 60s — generous for slower machines

async function fetchAddressSchema(locale, ollamaUrl = 'http://localhost:11434') {
  const country = getCountryCode(locale)
  const lang = getLanguageCode(locale)

  // Check cache first
  const cacheKey = `${country}_${lang}`
  if (schemaCache[cacheKey]) {
    console.log(`[AddressForm] Cache hit for ${cacheKey}`)
    return schemaCache[cacheKey]
  }

  console.log(`[AddressForm] === Starting schema fetch ===`)
  console.log(`[AddressForm] Country: ${country}, Language: ${lang}`)
  console.log(`[AddressForm] Model: ${OLLAMA_MODEL}`)
  console.log(`[AddressForm] Endpoint: ${ollamaUrl}/api/generate`)
  console.log(`[AddressForm] Timeout: ${OLLAMA_TIMEOUT_MS / 1000}s`)

  // ─── RAG Step: Fetch ground truth from Google's address metadata ───
  // This is the "retrieval" in Retrieval-Augmented Generation.
  // We fetch real address format data, then feed it to the LLM as context
  // so it generates from facts instead of hallucinating.
  let googleMetadata = null
  try {
    console.log(`[AddressForm] RAG: Fetching Google address metadata for ${country}...`)
    const metaResponse = await fetch(
      `https://chromium-i18n.appspot.com/ssl-address/data/${country}`,
      { signal: AbortSignal.timeout(5000) } // 5s timeout — don't block if unavailable
    )
    if (metaResponse.ok) {
      googleMetadata = await metaResponse.json()
      console.log(`[AddressForm] RAG: Got metadata:`, JSON.stringify(googleMetadata).slice(0, 300))
    } else {
      console.log(`[AddressForm] RAG: Google returned HTTP ${metaResponse.status}, proceeding without`)
    }
  } catch (metaErr) {
    console.log(`[AddressForm] RAG: Could not fetch Google metadata (${metaErr.message}), proceeding without`)
  }

  // ─── Build context string from Google metadata ───
  // Build RAG context — translate Google metadata into DIRECT, SIMPLE instructions
  // that even a 1B model can follow. No abstraction, no "interpret this format string."
  let ragContext = ''
  if (googleMetadata) {
    const lines = []

    // Decode fmt string into explicit field order instruction
    if (googleMetadata.fmt) {
      const fieldMap = { '%Z': 'postal code', '%S': 'state/prefecture', '%C': 'city', '%A': 'street address', '%N': 'name', '%O': 'organization', '%D': 'district' }
      const order = googleMetadata.fmt.match(/%[A-Z]/g)
        ?.map(code => fieldMap[code])
        .filter(Boolean)
      if (order) {
        lines.push(`FIELD ORDER (you MUST follow this): ${order.join(' → ')}`)
        lines.push(`The FIRST field in the form must be: ${order[0]}`)
        lines.push(`The LAST field in the form must be: ${order[order.length - 1]}`)
      }
    }

    // Postal code — direct placeholder instruction
    if (googleMetadata.zipex) {
      const example = googleMetadata.zipex.split(',')[0]
      lines.push(`Postal code placeholder MUST be: "${example}"`)
    }

    // State name type — direct label instruction
    if (googleMetadata.state_name_type) {
      lines.push(`The state/province field is called "${googleMetadata.state_name_type}" in this country`)
    }

    // Subdivisions for select dropdown
    if (googleMetadata.sub_keys) {
      const keys = googleMetadata.sub_keys.split('~').slice(0, 8)
      lines.push(`Subdivisions (use as select options): ${keys.join(', ')}`)
    }

    ragContext = `\nOFFICIAL ADDRESS RULES FOR ${country}:\n${lines.join('\n')}\n`
    console.log(`[AddressForm] RAG context injected (${ragContext.length} chars)`)
  }

  // ─── Build prompt with RAG context ───
  const prompt = `Generate a JSON address form for country "${country}" with labels in "${lang}".
${ragContext}
Example for US:
{"country_code":"US","country_name":"United States","fields":[{"key":"street","label":"Street address","placeholder":"123 Main St","stripe_map":"line1","type":"text","width":"full"},{"key":"city","label":"City","placeholder":"San Francisco","stripe_map":"city","type":"text","width":"half","group":"r1"},{"key":"state","label":"State","placeholder":"CA","stripe_map":"state","type":"text","width":"half","group":"r1"},{"key":"postal","label":"ZIP code","placeholder":"94102","stripe_map":"postalCode","type":"text","width":"half"}],"field_order_note":"US: street then city/state/zip"}

Now generate for "${country}" in "${lang}". Rules:
- 4-6 fields: street, city, state/province, postal code, plus country-specific ones
- ALL labels in ${lang} language (e.g. "Adresse" not "Street" for French, "住所" for Japanese)
- Correct field ORDER for ${country} — follow the format string above if available
- stripe_map must be exactly ONE of: line1, line2, city, state, postalCode
- Use the postal code example from above as the placeholder if available
- Use "group" to put fields on same row

Return ONLY JSON for ${country}:`

  const startTime = Date.now()

  try {
    console.log(`[AddressForm] Sending request...`)
    console.log(`[AddressForm] Prompt length: ${prompt.length} chars`)

    const controller = new AbortController()
    const timeoutId = setTimeout(() => {
      console.error(`[AddressForm] ⏰ Request timed out after ${OLLAMA_TIMEOUT_MS / 1000}s`)
      controller.abort()
    }, OLLAMA_TIMEOUT_MS)

    const reqBody = {
      model: OLLAMA_MODEL,
      prompt,
      stream: false,
      options: { temperature: 0.1, num_predict: 800 },
    }
    console.log(`[AddressForm] Request body:`, JSON.stringify({ ...reqBody, prompt: `(${prompt.length} chars)` }))

    const response = await fetch(`${ollamaUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify(reqBody),
    })

    clearTimeout(timeoutId)
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
    console.log(`[AddressForm] ✅ Response received in ${elapsed}s — HTTP ${response.status}`)

    if (!response.ok) {
      const body = await response.text()
      console.error(`[AddressForm] HTTP error body:`, body.slice(0, 300))
      throw new Error(`Ollama HTTP ${response.status}: ${body.slice(0, 200)}`)
    }

    const data = await response.json()
    let text = (data.response || '').trim()

    console.log(`[AddressForm] Raw response (${text.length} chars):`)
    console.log(text.slice(0, 800))

    // Extract JSON from response — LLM might wrap it in markdown or add preamble
    text = text.replace(/^```json\s*/i, '').replace(/\s*```$/i, '').trim()

    // Extract and repair JSON from LLM output.
    // The model may add prefixes like <|python_tag|>, markdown fences,
    // preamble text, or truncate mid-field if num_predict runs out.

    // Step 1: Find the outermost JSON object
    const jsonStart = text.indexOf('{')
    if (jsonStart < 0) {
      throw new Error('No JSON object found in response')
    }
    text = text.slice(jsonStart)

    // Step 2: Try parsing as-is first
    let schema
    try {
      schema = JSON.parse(text)
    } catch (firstErr) {
      console.log(`[AddressForm] Direct parse failed, attempting repair...`)

      // Step 3: Truncation repair — find the last complete field object,
      // then close the fields array and outer object
      // Look for the last complete "}" that ends a field entry
      const lastCompleteField = text.lastIndexOf('},{')
      const lastFieldEnd = text.lastIndexOf('"}')

      // Pick the further position as the likely end of the last complete field
      let cutPoint = Math.max(lastCompleteField, lastFieldEnd)

      if (cutPoint > 0) {
        // Find what we need to close: count unclosed [ and {
        let repaired = text.slice(0, cutPoint + (text[cutPoint] === ',' ? 1 : 2))

        // Count brackets to figure out what's unclosed
        let braces = 0, brackets = 0
        for (const ch of repaired) {
          if (ch === '{') braces++
          if (ch === '}') braces--
          if (ch === '[') brackets++
          if (ch === ']') brackets--
        }

        // Close unclosed brackets and braces
        // Remove trailing comma if present
        repaired = repaired.replace(/,\s*$/, '')
        while (brackets > 0) { repaired += ']'; brackets-- }
        while (braces > 0) { repaired += '}'; braces-- }

        console.log(`[AddressForm] Repaired JSON (last 100 chars): ...${repaired.slice(-100)}`)

        try {
          schema = JSON.parse(repaired)
          console.log(`[AddressForm] ✅ Truncation repair successful`)
        } catch (repairErr) {
          console.error(`[AddressForm] ❌ Repair also failed:`, repairErr.message)
          console.error(`[AddressForm] Repaired text (last 200):`, repaired.slice(-200))
          throw new Error(`JSON parse failed even after repair: ${repairErr.message}`)
        }
      } else {
        console.error(`[AddressForm] ❌ Cannot find a repair point`)
        console.error(`[AddressForm] Text:`, text.slice(0, 500))
        throw new Error(`JSON parse failed: ${firstErr.message}`)
      }
    }

    // Validate basic structure
    if (!schema.fields || !Array.isArray(schema.fields) || schema.fields.length === 0) {
      console.error(`[AddressForm] ❌ Schema has no fields array:`, JSON.stringify(schema).slice(0, 300))
      throw new Error('Schema has no fields')
    }

    schema.country_code = schema.country_code || country

    // Fix any missing or invalid stripe_map values
    const validMaps = new Set(['name', 'line1', 'line2', 'city', 'state', 'postalCode'])
    let fixedCount = 0
    for (const field of schema.fields) {
      // Fix stripe_map based on field key if invalid
      if (!field.stripe_map || !validMaps.has(field.stripe_map)) {
        const k = (field.key || '').toLowerCase()
        if (/name|nom|nombre|氏名/i.test(k)) field.stripe_map = 'name'
        else if (/street|rue|calle|strasse|address|line1|endere/i.test(k)) field.stripe_map = 'line1'
        else if (/apt|suite|unit|line2|comp|bairro|neighbor/i.test(k)) field.stripe_map = 'line2'
        else if (/city|ville|ciudad|ort|cidade|市|town|locality/i.test(k)) field.stripe_map = 'city'
        else if (/state|province|region|prefecture|estado|dep|県|都道府県|canton/i.test(k)) field.stripe_map = 'state'
        else if (/postal|zip|plz|cep|〒|郵便|code_postal/i.test(k)) field.stripe_map = 'postalCode'
        else field.stripe_map = 'line2'
        fixedCount++
      }
      // Also fix: if key contains "postal" but stripe_map is wrong, override it
      if (/postal|zip|plz|cep|郵便/i.test(field.key || '') && field.stripe_map !== 'postalCode') {
        console.log(`[AddressForm] Fixing stripe_map for "${field.key}": ${field.stripe_map} → postalCode`)
        field.stripe_map = 'postalCode'
        fixedCount++
      }
    }

    // Deduplicate fields with the same key (LLM sometimes generates duplicates)
    const seenKeys = new Set()
    const deduped = []
    for (const field of schema.fields) {
      if (!seenKeys.has(field.key)) {
        seenKeys.add(field.key)
        deduped.push(field)
      } else {
        console.log(`[AddressForm] Removing duplicate key: "${field.key}"`)
      }
    }

    // Also dedup by stripe_map — if two fields map to the same Stripe key,
    // keep the first (the LLM often generates both "prefecture" and "state/province")
    const seenMaps = new Set()
    const dedupedByMap = []
    for (const field of deduped) {
      if (!seenMaps.has(field.stripe_map)) {
        seenMaps.add(field.stripe_map)
        dedupedByMap.push(field)
      } else {
        console.log(`[AddressForm] Removing redundant field "${field.key}" (duplicate stripe_map: ${field.stripe_map})`)
      }
    }

    if (dedupedByMap.length < schema.fields.length) {
      console.log(`[AddressForm] Deduped: ${schema.fields.length} → ${dedupedByMap.length} fields`)
    }
    schema.fields = dedupedByMap

    // ─── Label correction using Google metadata ───
    // The LLM often confuses labels (e.g. 住所="address" for postal code).
    // Use known translations keyed by stripe_map + language to fix them.
    const labelCorrections = {
      postalCode: {
        ja: '郵便番号', ko: '우편번호', zh: '邮政编码',
        de: 'Postleitzahl', fr: 'Code postal', es: 'Código postal',
        pt: 'CEP', fi: 'Postinumero', hi: 'पिन कोड',
      },
      state: {
        ja: '都道府県', ko: '시/도', zh: '省/自治区',
        de: 'Bundesland', fr: 'Département', es: 'Provincia',
        fi: 'Maakunta', hi: 'राज्य',
      },
      city: {
        ja: '市区町村', ko: '시/군/구', zh: '城市',
        de: 'Ort', fr: 'Ville', es: 'Ciudad',
        fi: 'Kaupunki', hi: 'शहर',
      },
      line1: {
        ja: '住所', ko: '주소', zh: '详细地址',
        de: 'Straße', fr: 'Adresse', es: 'Dirección',
        fi: 'Osoite', hi: 'पता',
      },
      name: {
        ja: '氏名', ko: '이름', zh: '姓名',
        de: 'Name', fr: 'Nom complet', es: 'Nombre completo',
        fi: 'Nimi', hi: 'नाम',
      },
    }

    // Apply corrections: if the known label differs from LLM's label, override it
    for (const field of schema.fields) {
      const corrections = labelCorrections[field.stripe_map]
      if (corrections && corrections[lang]) {
        const correct = corrections[lang]
        if (field.label !== correct) {
          console.log(`[AddressForm] Label fix: "${field.key}" label "${field.label}" → "${correct}"`)
          field.label = correct
        }
      }
    }

    // Cache it
    schemaCache[cacheKey] = schema
    console.log(`[AddressForm] ✅ Schema ready for ${country}: ${schema.fields.length} fields${fixedCount > 0 ? ` (${fixedCount} stripe_map fixed)` : ''}`)
    console.log(`[AddressForm] Field order: ${schema.fields.map(f => `${f.key}→${f.stripe_map}`).join(', ')}`)
    if (schema.field_order_note) {
      console.log(`[AddressForm] Rationale: ${schema.field_order_note}`)
    }
    return schema

  } catch (err) {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
    if (err.name === 'AbortError') {
      console.error(`[AddressForm] ❌ TIMEOUT after ${elapsed}s — model "${OLLAMA_MODEL}" may be too slow`)
      console.error(`[AddressForm] 💡 Try a smaller model: edit OLLAMA_MODEL in AddressForm.jsx`)
      console.error(`[AddressForm] 💡 Options: "llama3.2:1b" (fastest), "phi3", "mistral"`)
      console.error(`[AddressForm] 💡 Run "ollama list" to see installed models`)
    } else {
      console.error(`[AddressForm] ❌ Error after ${elapsed}s:`, err.message || err)
      if (err.message?.includes('Failed to fetch')) {
        console.error(`[AddressForm] 💡 Is Ollama running? Check: curl http://localhost:11434/api/tags`)
        console.error(`[AddressForm] 💡 CORS issue? Set: OLLAMA_ORIGINS=* ollama serve`)
      }
    }
    console.log(`[AddressForm] Using fallback generic form`)
    const fallback = getFallbackSchema(country, lang)
    schemaCache[cacheKey] = fallback
    return fallback
  }
}

// ─── Fallback schema (generic international format if Ollama is down) ────────
function getFallbackSchema(country, lang) {
  return {
    country_code: country,
    country_name: country,
    fields: [
      { key: 'name', label: 'Full name', stripe_map: 'name', required: true, type: 'text', width: 'full', placeholder: '' },
      { key: 'line1', label: 'Street address', stripe_map: 'line1', required: true, type: 'text', width: 'full', placeholder: '' },
      { key: 'line2', label: 'Address line 2', stripe_map: 'line2', required: false, type: 'text', width: 'full', placeholder: '' },
      { key: 'city', label: 'City', stripe_map: 'city', required: true, type: 'text', width: 'half', placeholder: '', group: 'city_row' },
      { key: 'state', label: 'State / Province', stripe_map: 'state', required: false, type: 'text', width: 'half', placeholder: '', group: 'city_row' },
      { key: 'postalCode', label: 'Postal code', stripe_map: 'postalCode', required: true, type: 'text', width: 'half', placeholder: '' },
    ],
    field_order_note: 'Generic international format (Ollama unavailable)',
    _fallback: true,
  }
}

// ─── Normalize field values to Stripe billing_details shape ──────────────────
function normalizeToStripe(schema, values) {
  const result = { name: '', line1: '', line2: '', city: '', state: '', postalCode: '', country: schema.country_code }

  for (const field of schema.fields) {
    const stripeKey = field.stripe_map
    const val = (values[field.key] || '').trim()

    if (!stripeKey || !result.hasOwnProperty(stripeKey) || !val) continue

    // Concatenate if multiple fields map to the same Stripe key
    // (e.g. German street + house number both → line1)
    if (result[stripeKey]) {
      result[stripeKey] += ' ' + val
    } else {
      result[stripeKey] = val
    }
  }

  return result
}

// ─── Single field renderer ───────────────────────────────────────────────────
function DynamicField({ field, value, onChangeValue }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 12, color: 'var(--ink-muted)', marginBottom: 4 }}>
        {field.label}
        {field.label_en && field.label_en !== field.label && (
          <span style={{ fontSize: 11, opacity: 0.65, marginLeft: 5 }}>{field.label_en}</span>
        )}
        {!field.required && (
          <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 4 }}>(optional)</span>
        )}
      </label>

      {field.type === 'select' && field.options?.length > 0 ? (
        <select
          value={value || ''}
          onChange={(e) => onChangeValue(field.key, e.target.value)}
        >
          <option value="">—</option>
          {field.options.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      ) : (
        <input
          value={value || ''}
          onChange={(e) => onChangeValue(field.key, e.target.value)}
          placeholder={field.placeholder || ''}
          maxLength={field.max_length || undefined}
          inputMode={field.input_mode === 'numeric' ? 'numeric' : undefined}
        />
      )}

      {field.hint && (
        <p style={{ fontSize: 11, color: 'var(--ink-muted)', marginTop: 3, opacity: 0.75 }}>{field.hint}</p>
      )}
    </div>
  )
}

// ─── Dynamic form — groups fields into rows based on "group" property ────────
function DynamicAddressForm({ schema, values, onChange }) {
  const handleChange = (key, val) => {
    onChange({ ...values, [key]: val })
  }

  // Build rows: fields with same "group" value go on the same row
  const rows = []
  let currentGroup = null
  let currentRow = []

  for (const field of schema.fields) {
    if (field.group && field.group === currentGroup) {
      currentRow.push(field)
    } else {
      if (currentRow.length > 0) rows.push(currentRow)
      currentRow = [field]
      currentGroup = field.group || null
    }
  }
  if (currentRow.length > 0) rows.push(currentRow)

  return (
    <>
      {rows.map((row, ri) => {
        if (row.length === 1) {
          return (
            <DynamicField
              key={row[0].key}
              field={row[0]}
              value={values[row[0].key]}
              onChangeValue={handleChange}
            />
          )
        }
        return (
          <div key={`row-${ri}`} style={{ display: 'flex', gap: 8 }}>
            {row.map(field => {
              const flex = field.width === 'third' ? '0 0 33%' : '1'
              return (
                <div key={field.key} style={{ flex }}>
                  <DynamicField field={field} value={values[field.key]} onChangeValue={handleChange} />
                </div>
              )
            })}
          </div>
        )
      })}
    </>
  )
}

// ─── Loading indicator with live elapsed timer ──────────────────────────────
function LoadingIndicator({ country }) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const start = Date.now()
    const interval = setInterval(() => {
      setElapsed(((Date.now() - start) / 1000).toFixed(0))
    }, 500)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{
      padding: '1.5rem 1rem',
      color: 'var(--ink-muted)',
      fontSize: '0.85rem',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <div className="spinner" />
        <span>Asking Ollama for {country} address structure...</span>
        <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.75rem', opacity: 0.6 }}>{elapsed}s</span>
      </div>
      <p style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono, monospace)', opacity: 0.5 }}>
        POST http://localhost:11434/api/generate (model: {OLLAMA_MODEL})
      </p>
    </div>
  )
}

// ─── Main exported component ─────────────────────────────────────────────────
export default function AddressForm({ locale = 'en-US', onChange, ollamaUrl = 'http://localhost:11434' }) {
  const [values, setValues] = useState({})
  const [schema, setSchema] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const prevLocaleRef = useRef(null)
  const fetchIdRef = useRef(0) // Track which fetch is current

  // Fetch schema when locale changes
  useEffect(() => {
    if (locale === prevLocaleRef.current) return
    prevLocaleRef.current = locale

    const thisId = ++fetchIdRef.current // Increment fetch ID
    console.log(`[AddressForm] useEffect fired — locale=${locale}, fetchId=${thisId}`)

    setLoading(true)
    setError(null)
    setSchema(null)
    setValues({})

    fetchAddressSchema(locale, ollamaUrl)
      .then(s => {
        // Only apply if this is still the latest fetch
        if (fetchIdRef.current !== thisId) {
          console.log(`[AddressForm] Stale fetch ${thisId} ignored (current: ${fetchIdRef.current})`)
          return
        }
        console.log(`[AddressForm] Setting schema from fetch ${thisId}`)
        setSchema(s)
        setLoading(false)
        if (onChange) {
          onChange(normalizeToStripe(s, {}))
        }
      })
      .catch(err => {
        if (fetchIdRef.current !== thisId) return
        console.error('[AddressForm] Schema fetch failed:', err)
        setError('Could not generate address form')
        setLoading(false)
      })

    // No cleanup that cancels — we use fetchIdRef instead
  }, [locale, ollamaUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  // Propagate value changes to parent as normalized Stripe address
  const handleChange = useCallback((newValues) => {
    setValues(newValues)
    if (onChange && schema) {
      onChange(normalizeToStripe(schema, newValues))
    }
  }, [onChange, schema])

  // ── Loading state with elapsed timer ──
  if (loading) {
    return <LoadingIndicator country={getCountryCode(locale)} />
  }

  // ── Error state ──
  if (error) {
    return (
      <div style={{
        padding: '0.75rem',
        background: 'var(--danger-soft, #fef2f2)',
        color: 'var(--danger, #c62828)',
        borderRadius: 'var(--radius-sm, 6px)',
        fontSize: '0.85rem',
      }}>
        {error}
      </div>
    )
  }

  if (!schema) return null

  console.log(`[AddressForm] Rendering form — ${schema.fields.length} fields, country: ${schema.country_code}`)
  console.log(`[AddressForm] Current values:`, values)

  // Safety: wrap in try/catch so a bad schema doesn't crash the whole page
  let formContent
  try {
    formContent = <DynamicAddressForm schema={schema} values={values} onChange={handleChange} />
  } catch (renderErr) {
    console.error(`[AddressForm] ❌ Render error:`, renderErr)
    formContent = (
      <div style={{ color: 'var(--danger, red)', fontSize: '0.85rem', padding: '0.5rem' }}>
        Form render error: {renderErr.message}. Check console for details.
      </div>
    )
  }

  return (
    <div style={{ fontFamily: 'var(--font-sans, system-ui, sans-serif)' }}>
      {/* Debug badge: shows this form was AI-generated */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginBottom: 12,
        padding: '4px 8px',
        background: 'var(--accent-soft, #f0f0ff)',
        borderRadius: 'var(--radius-sm, 6px)',
        fontSize: 11,
        fontFamily: 'var(--font-mono, monospace)',
        color: 'var(--accent, #7c6ef0)',
      }}>
        <span>🤖</span>
        <span>
          AI-generated ({OLLAMA_MODEL}): {schema.country_name || schema.country_code}
          {schema._fallback && ' (fallback — Ollama unavailable)'}
        </span>
        {schema.field_order_note && (
          <span
            style={{ opacity: 0.7, marginLeft: 'auto', cursor: 'help' }}
            title={schema.field_order_note}
          >
            ⓘ
          </span>
        )}
      </div>

      {formContent}

      {/* Debug: raw schema dump */}
      <details style={{ marginTop: 12, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--ink-muted)' }}>
        <summary style={{ cursor: 'pointer', opacity: 0.6 }}>Debug: raw schema from LLM</summary>
        <pre style={{ whiteSpace: 'pre-wrap', marginTop: 4, padding: 8, background: 'var(--surface-elevated, #f5f5f5)', borderRadius: 4, maxHeight: 200, overflow: 'auto' }}>
          {JSON.stringify(schema, null, 2)}
        </pre>
      </details>
    </div>
  )
}

/**
 * Error types with stable exit codes. The CLI prints these as JSON when --json
 * is used, so an agent can branch on `code` instead of parsing prose.
 */
export class AppError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = options.name || 'AppError'
    this.code = options.code || 'ERROR'
    this.exitCode = options.exitCode ?? 1
    this.hints = Array.isArray(options.hints) ? options.hints : []
    this.details = options.details ?? null
  }
}

export class UsageError extends AppError {
  constructor(message, options = {}) {
    super(message, { ...options, name: 'UsageError', code: options.code || 'USAGE', exitCode: 2 })
  }
}

export class PrereqError extends AppError {
  constructor(message, options = {}) {
    super(message, { ...options, name: 'PrereqError', code: options.code || 'PREREQUISITE', exitCode: 3 })
  }
}

export class ExportError extends AppError {
  constructor(message, options = {}) {
    super(message, { ...options, name: 'ExportError', code: options.code || 'EXPORT_FAILED', exitCode: 4 })
  }
}

export class PartialError extends AppError {
  constructor(message, options = {}) {
    super(message, { ...options, name: 'PartialError', code: options.code || 'PARTIAL', exitCode: 5 })
  }
}

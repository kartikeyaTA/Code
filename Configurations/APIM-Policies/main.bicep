// ============================================================================
// Sub B — APIM backends, priority pool, API + policy, subscription key
// Assumes the APIM instance itself already exists (referenced, not created here).
// RBAC/role-assignment on the Sub C resources is intentionally NOT included —
// handle that separately (see comment near the bottom).
// ============================================================================

@description('Name of the existing APIM instance in this subscription')
param apimName string = 'apim-gateway-application-test-dev4'

@description('Primary Sub C model endpoint, e.g. https://foundry-services-model-dev15.openai.azure.com/openai/v1')
param primaryEndpoint string = 'https://foundry-services-model-dev17.openai.azure.com/'

@description('Secondary (failover) Sub C model endpoint')
param secondaryEndpoint string = 'https://foundry-services-model-dev18.openai.azure.com/'

@description('API URL suffix — the path segment clients call, e.g. "models"')
param apiSuffix string = 'models'

@description('Display name for the API')
param apiDisplayName string = 'Model Gateway'

var backendPoolName = 'openai-failover-pool'

param appInsightsName string = 'app-insights-ai-chat-dev'
param logAnalyticsName string = 'log-analytics-ai-chat-dev'
// --- Reference the existing APIM instance -----------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// --- Backend: primary ---------------------------------------------------------
resource backendPrimary 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'openai-primary'
  properties: {
    protocol: 'http'
    url: primaryEndpoint
    credentials: {
      managedIdentity: {
        resource: 'https://cognitiveservices.azure.com'
        // Note: Omitting 'clientIdentity' tells APIM to use the System-Assigned identity
      }
    }
    circuitBreaker: {
      rules: [
        {
          failureCondition: {
            count: 1
            interval: 'PT1M'
            statusCodeRanges: [
              { min: 400, max: 599 }
            ]
            errorReasons: [
              'OperationNotFound'
              'SubscriptionKeyNotFound'
              'SubscriptionKeyInvalid'
              'ClientConnectionFailure'
              'BackendConnectionFailure'
              'ExpressionValueEvaluationFailure'
            ]
          }
          name: 'primaryBreakerRule'
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

// --- Backend: secondary --------------------------------------------------------
resource backendSecondary 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'openai-secondary'
  properties: {
    protocol: 'http'
    url: secondaryEndpoint
    credentials: {
      managedIdentity: {
        resource: 'https://cognitiveservices.azure.com'
        // Note: Omitting 'clientIdentity' tells APIM to use the System-Assigned identity
      }
    }
    circuitBreaker: {
      rules: [
        {
          failureCondition: {
            count: 1
            interval: 'PT1M'
            statusCodeRanges: [
              { min: 400, max: 599 }
            ]
            errorReasons: [
              'OperationNotFound'
              'SubscriptionKeyNotFound'
              'SubscriptionKeyInvalid'
              'ClientConnectionFailure'
              'BackendConnectionFailure'
              'ExpressionValueEvaluationFailure'
            ]
          }
          name: 'secondaryBreakerRule'
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

// --- Priority pool combining both -----------------------------------------------
// NOTE: no `protocol` property here -- pool-type backends reject it.
resource backendPool 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: backendPoolName
  properties: {
    type: 'Pool'
    pool: {
      services: [
        { id: backendPrimary.id, priority: 1 }
        { id: backendSecondary.id, priority: 2 }
      ]
    }
  }
}


resource api 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'foundry-services'
  properties: {
    displayName: 'Chatting'
    apiRevision: '1'
    subscriptionRequired: true
    path: 'models'
    protocols: [
      'https'
    ]
    authenticationSettings: {
      oAuth2AuthenticationSettings: []
      openidAuthenticationSettings: []
    }
    subscriptionKeyParameterNames: {
      header: 'api-key'
      query: 'subscription-key'
    }
    license: {
      name: 'MIT'
      url: 'https://github.com/openai/openai-openapi/blob/master/LICENSE'
    }
    isCurrent: true
  }
}


resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'xml'
    value: '<policies>\r\n  <inbound>\r\n    <base />\r\n    <authentication-managed-identity resource="https://cognitiveservices.azure.com/" />\r\n    <set-backend-service id="route-to-pool" backend-id="${backendPoolName}" />\r\n  </inbound>\r\n  <backend>\r\n    <retry condition="@(context.Response == null || context.Response.StatusCode &gt;= 400)" count="3" interval="1" first-fast-retry="true">\r\n      <forward-request  buffer-request-body="true" />\r\n    </retry>\r\n  </backend>\r\n  <outbound>\r\n    <base />\r\n  </outbound>\r\n  <on-error>\r\n    <base />\r\n  </on-error>\r\n</policies>'
  }
  dependsOn: [
    apim
    backendPool
  ]
}


// --- Subscription key for Sub A to use ---------------------------------------
resource subscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: 'sub-a-agent-subscription'
  properties: {
    scope: api.id
    displayName: 'Sub A Agent Access'
    state: 'active'
  }
}

resource service_apim_gateway_application_test_dev1_name_routing_createResponse 'Microsoft.ApiManagement/service/apis/operations@2025-09-01-preview' = {
  parent: api
  name: 'createResponse'
  properties: {
    displayName: 'Creates a model response.'
    method: 'POST'
    urlTemplate: '/openai/v1/responses'
    templateParameters: []
    description: 'Creates a model response.'
    request: {
      queryParameters: [
        {
          name: 'api-version'
          description: 'The explicit Microsoft Foundry Models API version to use for this request.\n`v1` if not otherwise specified.'
          type: 'string'
          values: [
            'v1'
            'preview'
          ]
        }
      ]
      headers: []
      representations: [
        {
          contentType: 'application/json'
          examples: {
            default: {
              value: {
                model: 'gpt-4o'
                input: 'How are you?'
                stream: false
              }
            }
          }
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'The request has succeeded.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  metadata: {}
                  temperature: json('0')
                  top_p: json('0')
                  user: 'string'
                  top_logprobs: 0
                  previous_response_id: 'string'
                  reasoning: {
                    effort: 'medium'
                    summary: 'auto'
                    generate_summary: null
                  }
                  background: false
                  max_output_tokens: 0
                  max_tool_calls: 0
                  text: {
                    format: {
                      type: {}
                    }
                  }
                  tools: [
                    {
                      type: {}
                    }
                  ]
                  tool_choice: {}
                  prompt: {
                    id: 'string'
                    version: 'string'
                    variables: {}
                  }
                  truncation: 'disabled'
                  id: 'string'
                  object: 'response'
                  status: 'completed'
                  created_at: 0
                  error: {
                    code: 'server_error'
                    message: 'string'
                  }
                  incomplete_details: {
                    reason: 'max_output_tokens'
                  }
                  output: [
                    {
                      type: 'message'
                      id: 'string'
                    }
                  ]
                  instructions: {}
                  output_text: 'string'
                  usage: {
                    input_tokens: 0
                    input_tokens_details: {
                      cached_tokens: 0
                    }
                    output_tokens: 0
                    output_tokens_details: {
                      reasoning_tokens: 0
                    }
                    total_tokens: 0
                  }
                  parallel_tool_calls: true
                  model: 'string'
                }
              }
            }
          }
          {
            contentType: 'text/event-stream'
            examples: {
              default: {}
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
      {
        statusCode: 400
        description: 'An unexpected error response.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  error: {
                    code: 'string'
                    message: 'string'
                    param: 'string'
                    type: 'error'
                    inner_error: {}
                  }
                }
              }
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
      {
        statusCode: 500
        description: 'An unexpected error response.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  error: {
                    code: 'string'
                    message: 'string'
                    param: 'string'
                    type: 'error'
                    inner_error: {}
                  }
                }
              }
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
    ]
  }
  dependsOn: [
    apim
  ]
}


resource chat 'Microsoft.ApiManagement/service/apis/operations@2025-09-01-preview' = {
  parent: api
  name: 'createChatCompletion'
  properties: {
    displayName: 'Creates a chat completion.'
    method: 'POST'
    urlTemplate: '/openai/v1/chat/completions'
    templateParameters: []
    description: 'Creates a chat completion.'
    request: {
      queryParameters: [
        {
          name: 'api-version'
          description: 'The explicit Microsoft Foundry Models API version to use for this request.\n`v1` if not otherwise specified.'
          type: 'string'
          values: [
            'v1'
            'preview'
          ]
        }
      ]
      headers: []
      representations: [
        {
          contentType: 'application/json'
          examples: {
            default: {
              value: {
                model: 'gpt-4o'
                messages: [
                  {
                    role: 'system'
                    content: 'You are a helpful assistant'
                  }
                  {
                    role: 'user'
                    content: 'How are you?'
                  }
                ]
                max_tokens: 50
              }
            }
          }
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'The request has succeeded.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  id: 'string'
                  created: 0
                  model: 'string'
                  system_fingerprint: 'string'
                  object: 'chat.completion'
                  usage: {
                    completion_tokens: 0
                    prompt_tokens: 0
                    total_tokens: 0
                    completion_tokens_details: {
                      accepted_prediction_tokens: 0
                      audio_tokens: 0
                      reasoning_tokens: 0
                      rejected_prediction_tokens: 0
                    }
                    prompt_tokens_details: {
                      audio_tokens: 0
                      cached_tokens: 0
                    }
                  }
                  choices: [
                    {
                      finish_reason: 'stop'
                      index: 0
                      logprobs: {
                        content: [
                          {
                            token: 'string'
                            logprob: json('0')
                            bytes: [
                              0
                            ]
                            top_logprobs: [
                              {
                                token: 'string'
                                logprob: json('0')
                                bytes: [
                                  0
                                ]
                              }
                            ]
                          }
                        ]
                        refusal: [
                          {
                            token: 'string'
                            logprob: json('0')
                            bytes: [
                              0
                            ]
                            top_logprobs: [
                              {
                                token: 'string'
                                logprob: json('0')
                                bytes: [
                                  0
                                ]
                              }
                            ]
                          }
                        ]
                      }
                      content_filter_results: {
                        sexual: {
                          filtered: true
                          severity: 'safe'
                        }
                        hate: {
                          filtered: true
                          severity: 'safe'
                        }
                        violence: {
                          filtered: true
                          severity: 'safe'
                        }
                        self_harm: {
                          filtered: true
                          severity: 'safe'
                        }
                        profanity: {
                          filtered: true
                          detected: true
                        }
                        custom_blocklists: {
                          filtered: true
                          details: [
                            {
                              filtered: true
                              id: 'string'
                            }
                          ]
                        }
                        custom_topics: {
                          filtered: true
                          details: [
                            {
                              detected: true
                              id: 'string'
                            }
                          ]
                        }
                        error: {
                          code: 0
                          message: 'string'
                        }
                        protected_material_text: {
                          filtered: true
                          detected: true
                        }
                        protected_material_code: {
                          filtered: true
                          detected: true
                          citation: {
                            license: 'string'
                            URL: 'string'
                          }
                        }
                        ungrounded_material: {
                          filtered: true
                          detected: true
                          details: [
                            {
                              completion_start_offset: 0
                              completion_end_offset: 0
                            }
                          ]
                        }
                        personally_identifiable_information: {
                          redacted_text: 'string'
                          sub_categories: [
                            {
                              sub_category: 'string'
                              filtered: true
                              detected: true
                              redacted: true
                            }
                          ]
                          filtered: true
                          detected: true
                        }
                      }
                      message: {
                        content: 'string'
                        refusal: 'string'
                        tool_calls: [
                          {
                            id: 'string'
                            type: 'function'
                            function: {
                              name: 'string'
                              arguments: 'string'
                            }
                          }
                        ]
                        annotations: [
                          {
                            type: 'url_citation'
                            url_citation: {
                              end_index: 0
                              start_index: 0
                              url: 'string'
                              title: 'string'
                            }
                          }
                        ]
                        role: 'assistant'
                        function_call: {
                          name: 'string'
                          arguments: 'string'
                        }
                        audio: {
                          id: 'string'
                          expires_at: 0
                          data: 'string'
                          transcript: 'string'
                        }
                        context: {
                          intent: 'string'
                          citations: [
                            {
                              content: 'string'
                              title: 'string'
                              url: 'string'
                              filepath: 'string'
                              chunk_id: 'string'
                              rerank_score: json('0')
                            }
                          ]
                          all_retrieved_documents: {
                            content: 'string'
                            title: 'string'
                            url: 'string'
                            filepath: 'string'
                            chunk_id: 'string'
                            rerank_score: json('0')
                            search_queries: [
                              'string'
                            ]
                            data_source_index: 0
                            original_search_score: json('0')
                            filter_reason: 'score'
                          }
                        }
                        reasoning_content: 'string'
                      }
                    }
                  ]
                  prompt_filter_results: [
                    {
                      prompt_index: 0
                      content_filter_results: {
                        prompt_index: 0
                        content_filter_results: {
                          sexual: {
                            filtered: true
                            severity: 'safe'
                          }
                          hate: {
                            filtered: true
                            severity: 'safe'
                          }
                          violence: {
                            filtered: true
                            severity: 'safe'
                          }
                          self_harm: {
                            filtered: true
                            severity: 'safe'
                          }
                          profanity: {
                            filtered: true
                            detected: true
                          }
                          custom_blocklists: {
                            filtered: true
                            details: [
                              {
                                filtered: true
                                id: 'string'
                              }
                            ]
                          }
                          custom_topics: {
                            filtered: true
                            details: [
                              {
                                detected: true
                                id: 'string'
                              }
                            ]
                          }
                          error: {
                            code: 0
                            message: 'string'
                          }
                          jailbreak: {
                            filtered: true
                            detected: true
                          }
                          indirect_attack: {
                            filtered: true
                            detected: true
                          }
                        }
                      }
                    }
                  ]
                }
              }
            }
          }
          {
            contentType: 'text/event-stream'
            examples: {
              default: {}
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
      {
        statusCode: 400
        description: 'An unexpected error response.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  error: {
                    code: 'string'
                    message: 'string'
                    param: 'string'
                    type: 'error'
                    inner_error: {}
                  }
                }
              }
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
      {
        statusCode: 500
        description: 'An unexpected error response.'
        representations: [
          {
            contentType: 'application/json'
            examples: {
              default: {
                value: {
                  error: {
                    code: 'string'
                    message: 'string'
                    param: 'string'
                    type: 'error'
                    inner_error: {}
                  }
                }
              }
            }
          }
        ]
        headers: [
          {
            name: 'apim-request-id'
            description: 'A request ID used for troubleshooting purposes.'
            type: 'string'
            values: []
          }
        ]
      }
    ]
  }
  dependsOn: [
    apim
  ]
}


resource apimAppInsightsLogger 'Microsoft.ApiManagement/service/loggers@2025-09-01-preview' = {
  parent: apim
  name: appInsightsName // Using the name as the lookup ID inside APIM
  properties: {
    loggerType: 'applicationInsights'
    description: 'Application Insights Logger linked via Bicep'
    credentials: {
      connectionString: appInsights.properties.ConnectionString
    }
  }
}

resource apimDiagnosticToLogAnalytics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'apim-gateway-telemetry'
  scope: apim // Binds directly to your APIM gateway instance instance
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      {
        category: 'GatewayLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource azureMonitorDiagnostic 'Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview' = {
  parent: api
  name: 'azuremonitor' // Built-in system reserved keyword name
  properties: {
    loggerId: '${apim.id}/loggers/azuremonitor'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    alwaysLog: 'allErrors'
    logClientIp: true
    verbosity: 'information'

    largeLanguageModel: {
      logs: 'enabled'
      requests: {
        messages: 'all'
        maxSizeInBytes: 32768
      }
      responses: {
        messages: 'all'
        maxSizeInBytes: 32768
      }
    }
  }
}

resource appInsightsDiagnostic 'Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview' = {
  parent: api
  name: 'applicationinsights' // Built-in system reserved keyword name
  properties: {
    // 🎯 DYNAMIC FIX: References the runtime ID of the logger bridge we generated in step 3
    loggerId: apimAppInsightsLogger.id
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    alwaysLog: 'allErrors'
    logClientIp: true
    verbosity: 'information'
    metrics: true
    httpCorrelationProtocol: 'Legacy'
  }
}

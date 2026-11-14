# NutriPilot — Backend System Design & API Implementation Plan

**Document type:** Backend Architecture and API Specification  
**Product:** NutriPilot  
**Version:** 2.0  
**Architecture:** Modular backend + AI orchestration + SSE streaming  
**API style:** REST with SSE streaming for AI operations  
**API prefix:** `/api/v1`  
**Initial target:** Hackathon MVP  
**Primary clients:** Web application and mobile application  
**External integrations:** Careem MCP, AI services, nutrition and food data providers

---

# 1. Executive Summary

NutriPilot is an AI-powered personal nutrition and food management application that helps users plan meals, manage nutrition, discover restaurant meals, prepare grocery lists and manage food budgets.

The application supports three meal preparation modes:

1. Home-cooked meals.
2. Ready-made restaurant meals.
3. Hybrid meal plans combining both approaches.

The backend is responsible for managing the entire lifecycle, from user onboarding and meal plan generation to meal modifications, grocery consolidation, purchase authorization, scheduled actions and order tracking.

The AI service is accessed exclusively through the backend. AI responses are streamed to the backend using Server-Sent Events (SSE), and the backend forwards the appropriate events to the frontend using SSE.

The frontend never communicates directly with the AI service or Careem MCP.

## 1.1 Core architectural principle

**AI proposes → Backend validates → User authorizes → Integration executes → Backend tracks.**

This separation ensures that:
- AI-generated information is validated before being persisted.
- Nutrition calculations are handled by deterministic backend logic.
- Restaurant and grocery data originate from actual providers.
- Purchases are controlled by explicit user authorization.
- Scheduled actions are executed reliably.
- The application can support multiple AI and commerce providers in the future.

# 2. Product Decisions and Functional Scope

The following decisions are considered the baseline for implementation.

| Area | Decision |
|---|---|
| Meal preparation | Home-cooked, ready-made or hybrid |
| Plan duration | 7, 14, 30 days or custom range |
| Initial MVP | Prioritize a complete 7-day experience |
| Meal modification | Replace, skip, switch mode, regenerate |
| Modification after activation | Supported, subject to order cutoffs |
| Grocery purchasing | One consolidated grocery list/basket per delivery batch |
| Restaurant meals | Actual restaurant and menu data required |
| Nutrition | Backend calculation using structured food data |
| Purchase approval | Plan-level approval within explicit limits |
| Purchases | Only through verified, authorized provider capabilities |
| AI communication | Backend calls AI using SSE |
| Client communication | Backend forwards SSE to frontend |
| Background operations | Queue workers and persistent scheduled actions |
| Completed meals | Read-only |
| External integrations | Provider adapters, starting with Careem MCP |

## 2.1 Important product constraints

- Users must be able to choose a different preparation mode for each meal.
- Do not assume nationality determines cuisine preferences.
- Allergies are hard constraints.
- Never claim a restaurant meal is allergen-safe without sufficient verification.
- Do not invent restaurant names, menu items, prices or availability.
- AI-generated nutrition values are not authoritative.
- Price changes outside the approved tolerance require additional approval.
- Careem payment authorization requirements must be respected independently of NutriPilot plan approval.

# 3. High-Level System Architecture

```text
                    WEB APPLICATION
                    MOBILE APPLICATION
                           |
                           |
                     REST / SSE API
                           |
                    API GATEWAY
                           |
                  AUTHENTICATION / GUARDS
                           |
                  NUTRIPILOT BACKEND
                           |
       +-------------------+--------------------+
       |                   |                    |
   USER MODULE          MEAL MODULE          ORDER MODULE
       |                   |                    |
       +-------------------+--------------------+
                           |
                    AI ORCHESTRATOR
                           |
                   HTTP STREAMING CLIENT
                           |
                     AI SERVICE
                           |
                    SSE RESPONSE
                           |
                  BACKEND EVENT PARSER
                           |
                 VALIDATION / ENRICHMENT
                           |
                  PUBLIC SSE EVENT MAPPER
                           |
                     CLIENT SSE
                           
                           |
                  BUSINESS SERVICES
                           |
       +-------------------+-------------------+
       |                   |                   |
   NUTRITION             BUDGET             INGREDIENT
    ENGINE             CALCULATOR           PROCESSOR
       |                   |                   |
       +-------------------+-------------------+
                           |
                   INTEGRATION ADAPTERS
                           |
                      CAREEM MCP
                           |
               +-----------+-----------+
               |                       |
          RESTAURANTS               GROCERIES
          AND MENUS                AND PRODUCTS

                           |
                    QUEUE / SCHEDULER
                           |
                  PERSISTENT ACTIONS
                           |
                   NOTIFICATION SERVICE
```

## 3.1 Main architectural layers

### Application layer
Handles HTTP requests, authentication, validation, authorization and response formatting.

### Domain/business layer
Contains meal plan management, nutrition, grocery consolidation, budget, authorization and order rules.

### AI orchestration layer
Communicates with the AI service, streams AI events, validates structured AI output and maps events to the public API contract.

### Integration layer
Connects to Careem MCP and nutrition/food data providers.

### Background processing layer
Executes scheduled actions, synchronizes orders and sends notifications.

# 4. Communication Architecture

This is a critical design decision.

There are two communication paths.

## 4.1 Normal REST communication

Used for regular CRUD and business operations.

```text
Frontend
   |
   | HTTP Request
   |
Backend API
   |
   | Business Logic
   |
Database
   |
   | JSON Response
   |
Frontend
```

Examples:
- User profile.
- Preferences.
- Meal plan retrieval.
- Grocery list retrieval.
- Budget summary.
- Order history.

## 4.2 AI streaming communication

All AI-driven operations use an SSE streaming connection.

```text
Frontend
   |
   | POST /api/v1/.../stream
   |
Backend API
   |
   | Build AI Context
   |
AI Orchestrator
   |
   | HTTP Streaming Request
   |
AI Service
   |
   | SSE Events
   |
Backend SSE Parser
   |
   | Validate / Transform / Enrich
   |
Backend SSE Event Mapper
   |
   | SSE Events
   |
Frontend
```

The backend must not wait for the complete AI response before sending useful events to the frontend.

The backend should forward progress and content events immediately, while structured results are forwarded only after the relevant validation and enrichment steps.

## 4.3 Streaming endpoint convention

Use `/stream` suffix for AI operations.

Examples:

```text
POST /api/v1/meal-plans/{id}/generate/stream

POST /api/v1/meal-plans/{id}/regenerate/stream

POST /api/v1/recipes/generate/stream

POST /api/v1/meals/{id}/replace/stream

POST /api/v1/meals/{id}/switch-mode/stream

POST /api/v1/assistant/conversations/{id}/messages/stream
```

Use normal JSON endpoints for regular reads, writes and operations that do not require AI streaming.

## 4.4 Standard SSE response headers

```http
Content-Type: text/event-stream
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no
```

Authenticate and authorize the request before starting the stream.

## 4.5 Standard public SSE event contract

NutriPilot should maintain its own event contract rather than exposing raw AI provider events.

| Event | Purpose |
|---|---|
| `stream.started` | Connection and request initialized |
| `ai.thinking` | Optional AI progress message |
| `ai.content.delta` | Incremental assistant text |
| `generation.progress` | Generation workflow progress |
| `generation.result` | Validated partial result |
| `generation.completed` | Business generation operation completed |
| `assistant.action.proposed` | Proposed user action |
| `stream.heartbeat` | Keep-alive |
| `stream.completed` | Stream ended successfully |
| `stream.error` | Stream error |

Example:

```text
event: stream.started
data: {"request_id":"req_123"}

event: generation.progress
data: {"stage":"generating_meals","completed":1,"total":7}

event: generation.result
data: {"type":"day_generated","day":1,"meals":[]}

event: generation.progress
data: {"stage":"validating_nutrition"}

event: generation.completed
data: {"meal_plan_id":"plan_123"}

event: stream.completed
data: {"request_id":"req_123"}

```

All events should have consistent JSON data structures and request identifiers.

For extensibility, include a `version` field in the event envelope.

## 4.6 SSE parsing requirements

The backend must:
- Parse SSE incrementally.
- Handle events split across network chunks.
- Handle multiple events within one network chunk.
- Support multi-line `data:` fields.
- Buffer incomplete events.
- Handle comments and heartbeat events.
- Support the AI provider's termination convention, such as `[DONE]`, if applicable.
- Handle upstream errors and downstream disconnects.
- Never assume one network chunk equals one SSE event.

## 4.7 Client-side SSE implementation

Because our APIs require POST bodies and bearer-token authentication, use Fetch streaming rather than native browser `EventSource`.

Web client:

```typescript
const response = await fetch(
  `/api/v1/meal-plans/${planId}/generate/stream`,
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      'Accept': 'text/event-stream'
    },
    body: JSON.stringify({
      additional_instructions: 'High-protein meals'
    }),
    signal: abortController.signal
  }
);

if (!response.ok || !response.body) {
  throw new Error('Unable to start generation');
}

const reader = response.body.getReader();
```

Use a standards-compliant SSE parser rather than splitting individual chunks on newline characters.

For mobile, use an HTTP client that supports incremental response streaming.

# 5. Backend Module Structure

Recommended modular structure:

```text
src/
├── modules/
│   ├── auth/
│   ├── users/
│   ├── profiles/
│   ├── preferences/
│   ├── nutrition-goals/
│   ├── meal-plans/
│   ├── meals/
│   ├── recipes/
│   ├── restaurants/
│   ├── groceries/
│   ├── budget/
│   ├── authorizations/
│   ├── orders/
│   ├── schedules/
│   ├── notifications/
│   ├── assistant/
│   └── integrations/
│
├── ai/
│   ├── ai-orchestrator.service.ts
│   ├── ai-stream.service.ts
│   ├── ai-event.mapper.ts
│   ├── ai-event.types.ts
│   ├── providers/
│   ├── prompts/
│   ├── schemas/
│   └── validators/
│
├── integrations/
│   ├── careem/
│   │   ├── careem.adapter.ts
│   │   ├── careem.client.ts
│   │   ├── careem.types.ts
│   │   └── careem.mapper.ts
│   └── nutrition/
│
├── common/
│   ├── guards/
│   ├── interceptors/
│   ├── filters/
│   ├── decorators/
│   ├── streaming/
│   │   ├── sse.service.ts
│   │   ├── sse.parser.ts
│   │   └── sse.types.ts
│   └── utilities/
│
└── jobs/
    ├── meal-plan/
    ├── grocery/
    ├── orders/
    └── notifications/
```

# 6. API Specification

Base URL:

```text
/api/v1
```

## 6.1 Authentication APIs

Base path: `/auth`

| Method | Endpoint | Description |
|---|---|---|
| POST | `/auth/register` | Register user |
| POST | `/auth/login` | Login |
| POST | `/auth/refresh` | Refresh token |
| POST | `/auth/logout` | Logout |
| GET | `/auth/me` | Get authenticated user |

## 6.2 Profile and onboarding APIs

Base path: `/profile`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/profile` | Get user profile |
| PUT | `/profile` | Update basic profile |
| PUT | `/profile/body` | Update body information |
| PUT | `/profile/goals` | Update nutrition goals |
| PUT | `/profile/preferences` | Update food preferences |
| PUT | `/profile/restrictions` | Update allergies and restrictions |
| PUT | `/profile/routine` | Update daily routine |
| PUT | `/profile/budget` | Update budget |
| PUT | `/profile/cooking` | Update cooking preferences |
| GET | `/profile/onboarding-status` | Get onboarding progress |

Example:

`PUT /api/v1/profile/preferences`

```json
{
  "cuisines": ["indian", "arabic", "mediterranean"],
  "diet_type": "non_vegetarian",
  "preferred_proteins": ["chicken", "fish", "eggs"],
  "disliked_foods": ["mushrooms"],
  "spice_level": "medium"
}
```

Example restrictions:

```json
{
  "allergies": ["peanuts", "shellfish"],
  "dietary_restrictions": ["halal"]
}
```

Example nutrition goals:

```json
{
  "primary_goal": "weight_loss",
  "target_weight_kg": 72,
  "activity_level": "moderately_active",
  "secondary_goals": ["increase_protein"]
}
```

The backend should calculate and validate nutrition targets. Medical and special dietary requirements should not be inferred by AI.

## 6.3 Meal plan APIs

Base path: `/meal-plans`

| Method | Endpoint | Response |
|---|---|---|
| POST | `/meal-plans` | JSON |
| GET | `/meal-plans` | JSON |
| GET | `/meal-plans/{id}` | JSON |
| PUT | `/meal-plans/{id}` | JSON |
| POST | `/meal-plans/{id}/generate/stream` | SSE |
| POST | `/meal-plans/{id}/regenerate/stream` | SSE |
| POST | `/meal-plans/{id}/validate` | JSON |
| POST | `/meal-plans/{id}/activate` | JSON |
| POST | `/meal-plans/{id}/pause` | JSON |
| POST | `/meal-plans/{id}/cancel` | JSON |
| GET | `/meal-plans/{id}/summary` | JSON |
| GET | `/meal-plans/{id}/calendar` | JSON |
| GET | `/meal-plans/{id}/generation-status` | JSON |

### Create meal plan

`POST /api/v1/meal-plans`

```json
{
  "name": "My Weekly Nutrition Plan",
  "start_date": "2026-11-16",
  "duration_days": 7,
  "meal_types": ["breakfast", "lunch", "dinner"],
  "meal_mode": "hybrid",
  "servings": 1,
  "budget": {
    "amount": 600,
    "currency": "AED",
    "period": "week"
  },
  "meal_preferences": {
    "breakfast": "home_cooked",
    "lunch": "ready_made",
    "dinner": "home_cooked"
  },
  "timezone": "Asia/Dubai"
}
```

### Generate meal plan

`POST /api/v1/meal-plans/{id}/generate/stream`

```json
{
  "additional_instructions": "Prefer high-protein Indian meals."
}
```

Backend workflow:
1. Authenticate user and verify plan ownership.
2. Validate plan configuration.
3. Retrieve profile and preferences.
4. Create generation request record.
5. Call AI service through the streaming client.
6. Receive and parse SSE events.
7. Transform and validate structured results.
8. Enrich recipes and ready-made meal candidates.
9. Calculate nutrition and costs.
10. Persist the completed or partially completed result.
11. Forward progress and validated result events to the client.

The endpoint should not use ordinary polling as the primary interaction mechanism. SSE is the primary generation response.

## 6.4 Meal APIs

Base path: `/meals`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/meals/{id}` | JSON |
| PUT | `/meals/{id}` | JSON |
| POST | `/meals/{id}/replace/stream` | SSE |
| POST | `/meals/{id}/skip` | JSON |
| POST | `/meals/{id}/switch-mode/stream` | SSE |
| POST | `/meals/{id}/regenerate/stream` | SSE |
| POST | `/meals/{id}/servings` | JSON |
| GET | `/meals/{id}/alternatives/stream` | SSE |
| POST | `/meals/{id}/complete` | JSON |
| POST | `/meals/{id}/rate` | JSON |

### Replace a meal

`POST /api/v1/meals/{id}/replace/stream`

```json
{
  "reason": "Something higher in protein and cheaper",
  "preferred_mode": "home_cooked",
  "constraints": {
    "max_preparation_time_minutes": 30,
    "max_cost": 25
  }
}
```

The backend returns alternative candidates through SSE. The frontend displays the choices, and the user selects one. The backend then commits the selected change after validation.

### Switch meal mode

`POST /api/v1/meals/{id}/switch-mode/stream`

```json
{
  "target_mode": "ready_made"
}
```

For home-cooked to ready-made:
- Retrieve actual available menu candidates.
- Send candidates to AI for ranking.
- Validate selected item IDs.
- Recalculate nutrition and budget.

For ready-made to home-cooked:
- Generate recipe.
- Resolve ingredients.
- Calculate nutrition.
- Update consolidated grocery list.
- Recalculate budget.

### Skip meal

`POST /api/v1/meals/{id}/skip`

```json
{
  "reason": "Not required"
}
```

Skipping updates nutrition, grocery requirements, budget and scheduled actions where applicable.

## 6.5 Recipe APIs

Base path: `/recipes`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/recipes/{id}` | JSON |
| POST | `/recipes/generate/stream` | SSE |
| POST | `/recipes/{id}/recalculate` | JSON |
| POST | `/recipes/{id}/scale` | JSON |
| POST | `/recipes/{id}/substitutions/stream` | SSE |

Recipe generation should return structured data:

```json
{
  "name": "Chicken Rice Bowl",
  "servings": 1,
  "ingredients": [
    {
      "name": "Chicken breast",
      "quantity": 150,
      "unit": "g"
    },
    {
      "name": "Rice",
      "quantity": 75,
      "unit": "g",
      "quantity_type": "dry"
    }
  ],
  "instructions": [
    "Prepare the chicken.",
    "Cook the rice.",
    "Grill and serve."
  ],
  "preparation_time_minutes": 15,
  "cooking_time_minutes": 20
}
```

AI generates recipe content. Backend resolves food identifiers, validates units, calculates nutrition and checks restrictions.

## 6.6 Grocery APIs

Base path: `/groceries`

| Method | Endpoint | Response |
|---|---|---|
| POST | `/meal-plans/{id}/groceries/generate` | JSON |
| GET | `/meal-plans/{id}/groceries` | JSON |
| PUT | `/grocery-items/{id}` | JSON |
| POST | `/grocery-items/{id}/pantry` | JSON |
| POST | `/meal-plans/{id}/groceries/match-products` | JSON |
| GET | `/meal-plans/{id}/groceries/products` | JSON |
| POST | `/meal-plans/{id}/groceries/basket` | JSON |
| GET | `/meal-plans/{id}/groceries/basket` | JSON |
| POST | `/meal-plans/{id}/groceries/checkout` | JSON |

### Consolidated grocery workflow

1. Retrieve all home-cooked meals.
2. Retrieve recipe ingredients.
3. Normalize ingredient names and units.
4. Aggregate duplicate ingredients.
5. Deduct confirmed pantry quantities.
6. Calculate required quantities.
7. Match actual Careem grocery products.
8. Select suitable pack sizes.
9. Present the consolidated basket.
10. Submit only after authorization checks.

The grocery list is consolidated across the plan. For longer plans, split purchases into delivery batches when necessary for freshness, storage or delivery availability.

Do not use AI for basic quantity aggregation or arithmetic.

## 6.7 Restaurant APIs

Base path: `/restaurants`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/restaurants` | JSON |
| GET | `/restaurants/{id}` | JSON |
| GET | `/restaurants/{id}/menu` | JSON |
| GET | `/menu-items/{id}` | JSON |
| POST | `/restaurants/recommendations/stream` | SSE |
| GET | `/menu-items/{id}/nutrition` | JSON |

Restaurant discovery must retrieve actual provider data first.

AI receives actual menu candidates and ranks them based on:
- User preferences.
- Nutrition goals.
- Budget.
- Dietary restrictions.
- Cuisine.
- Meal context.

## 6.8 AI assistant APIs

Base path: `/assistant`

| Method | Endpoint | Response |
|---|---|---|
| POST | `/assistant/conversations` | JSON |
| GET | `/assistant/conversations` | JSON |
| GET | `/assistant/conversations/{id}` | JSON |
| POST | `/assistant/conversations/{id}/messages/stream` | SSE |
| POST | `/assistant/actions/{id}/confirm` | JSON |
| POST | `/assistant/actions/{id}/reject` | JSON |

Example:

`POST /api/v1/assistant/conversations/{id}/messages/stream`

```json
{
  "message": "Replace tomorrow's lunch with something cheaper.",
  "context": {
    "meal_plan_id": "plan_123",
    "meal_id": "meal_456"
  }
}
```

Expected event sequence:

```text
event: stream.started
data: {"request_id":"req_456"}

event: ai.content.delta
data: {"text":"I can help you find a cheaper lunch."}

event: ai.content.delta
data: {"text":" Let me compare some suitable alternatives."}

event: assistant.action.proposed
data: {
  "action_id":"action_123",
  "type":"replace_meal",
  "options":[]
}

event: stream.completed
data: {"request_id":"req_456"}

```

The AI may propose actions. The backend validates them and requires user confirmation where appropriate.

## 6.9 Budget APIs

Base path: `/budget`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/meal-plans/{id}/budget` | JSON |
| POST | `/meal-plans/{id}/budget/calculate` | JSON |
| PUT | `/meal-plans/{id}/budget` | JSON |
| GET | `/budget/summary` | JSON |

Budget breakdown:

```json
{
  "currency": "AED",
  "budget_limit": 600,
  "estimated_grocery_cost": 280,
  "estimated_restaurant_cost": 250,
  "estimated_delivery_fees": 20,
  "estimated_total": 550,
  "remaining_budget": 50
}
```

Use actual provider prices when available. Label estimates clearly.

## 6.10 Purchase authorization APIs

Base path: `/authorizations`

| Method | Endpoint | Response |
|---|---|---|
| POST | `/meal-plans/{id}/authorization` | JSON |
| GET | `/meal-plans/{id}/authorization` | JSON |
| POST | `/meal-plans/{id}/authorization/approve` | JSON |
| POST | `/meal-plans/{id}/authorization/revoke` | JSON |

Plan-level authorization should define:
- Maximum total spend.
- Grocery spending limit.
- Restaurant spending limit.
- Allowed purchase types.
- Price variation tolerance.
- Authorization validity.
- Provider-specific authorization status.

Example:

```json
{
  "max_total_spend": 600,
  "currency": "AED",
  "grocery_limit": 300,
  "restaurant_limit": 250,
  "allow_price_variation_percent": 5,
  "authorization_scope": [
    "scheduled_grocery_orders",
    "scheduled_restaurant_orders"
  ]
}
```

The backend must check the authorization immediately before executing every scheduled purchase.

A plan-level approval does not override Careem checkout or payment authorization requirements.

## 6.11 Order APIs

Base path: `/orders`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/orders` | JSON |
| GET | `/orders/{id}` | JSON |
| POST | `/orders/{id}/cancel` | JSON |
| GET | `/meal-plans/{id}/orders` | JSON |
| POST | `/orders/{id}/retry` | JSON |

Order types:
- `grocery`
- `restaurant`

Internal statuses:

`pending`, `processing`, `submitted`, `confirmed`, `preparing`, `out_for_delivery`, `delivered`, `failed`, `cancelled`, `needs_user_action`.

Map actual external provider statuses into this internal lifecycle.

## 6.12 Scheduling APIs

Base path: `/schedules`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/meal-plans/{id}/schedule` | JSON |
| PUT | `/meal-plans/{id}/schedule` | JSON |
| GET | `/scheduled-actions` | JSON |
| GET | `/scheduled-actions/{id}` | JSON |
| PUT | `/scheduled-actions/{id}` | JSON |
| POST | `/scheduled-actions/{id}/cancel` | JSON |

Scheduling configuration:

```json
{
  "restaurant_order_lead_time_minutes": 45,
  "home_cooking_reminder_lead_time_minutes": 90,
  "default_grocery_delivery_preference": "before_first_cooking_day"
}
```

These values are provisional defaults.

Distinguish:
- Restaurant order execution time.
- Restaurant requested delivery time.
- Home cooking preparation reminder.
- Grocery order execution time.
- Grocery delivery slot.

## 6.13 Notification APIs

Base path: `/notifications`

| Method | Endpoint | Response |
|---|---|---|
| GET | `/notifications` | JSON |
| PATCH | `/notifications/{id}/read` | JSON |
| PATCH | `/notifications/read-all` | JSON |
| GET | `/notifications/preferences` | JSON |
| PUT | `/notifications/preferences` | JSON |

Notifications include generation completion, activation, order status, failed actions, unavailable products, additional approval requirements and meal preparation reminders.

# 7. AI Orchestration Design

## 7.1 AI service boundary

Create an internal `AiOrchestratorService`.

Suggested methods:

```typescript
generateMealPlanStream()
generateRecipeStream()
replaceMealStream()
switchMealModeStream()
recommendRestaurantMealsStream()
processAssistantMessageStream()
suggestIngredientSubstitutionsStream()
```

These are internal backend methods, not public APIs.

## 7.2 AI operation lifecycle

```text
HTTP Controller
     |
     v
Business Service
     |
     v
AI Orchestrator
     |
     v
Build AI Context
     |
     v
AI Provider Client
     |
     v
Receive AI SSE
     |
     v
Parse AI Events
     |
     v
Validate Structured Output
     |
     v
Business Enrichment
     |
     v
Persist Results
     |
     v
Forward Public SSE Events
```

## 7.3 AI task: Meal plan generation

Input context:
- User profile.
- Body information.
- Nutrition goals.
- Food preferences.
- Allergies and restrictions.
- Meal mode.
- Plan duration.
- Meal schedule.
- Budget.
- Cooking preferences.

The AI should generate a structured meal plan proposal.

Example:

```json
{
  "task": "generate_meal_plan",
  "plan": {
    "duration_days": 7,
    "meal_types": ["breakfast", "lunch", "dinner"],
    "mode": "hybrid"
  },
  "preferences": {
    "cuisines": ["indian", "arabic"],
    "diet_type": "non_vegetarian",
    "allergies": ["peanuts"],
    "dietary_restrictions": ["halal"]
  }
}
```

AI output:

```json
{
  "days": [
    {
      "day": 1,
      "meals": [
        {
          "meal_type": "breakfast",
          "name": "Vegetable Omelette",
          "mode": "home_cooked",
          "recipe_required": true
        },
        {
          "meal_type": "lunch",
          "name": "Grilled Chicken Bowl",
          "mode": "ready_made",
          "restaurant_search_required": true
        }
      ]
    }
  ]
}
```

The backend then:
1. Generates or retrieves home-cooked recipes.
2. Retrieves actual restaurant candidates for ready-made meals.
3. Resolves ingredients.
4. Calculates nutrition.
5. Validates restrictions.
6. Calculates budget.
7. Persists the plan.
8. Streams validated results.

## 7.4 AI task: Recipe generation

AI returns:
- Recipe name.
- Ingredients.
- Quantities and units.
- Preparation instructions.
- Preparation time.
- Cooking time.
- Equipment requirements.
- Storage instructions where appropriate.

Backend validates ingredient structures and calculates nutrition.

## 7.5 AI task: Meal replacement

AI receives the current meal, user request and eligible alternatives.

For ready-made meals, the candidate list must originate from actual restaurant menu data.

AI ranks candidates. Backend verifies candidate IDs and recalculates affected plan data.

## 7.6 AI task: Restaurant recommendations

AI receives:
- Real menu candidates.
- Actual prices.
- Actual restaurant IDs.
- Available nutrition information.
- User preferences and constraints.

AI returns ranking and reasoning only.

Backend verifies that every selected candidate exists in the supplied set.

## 7.7 AI task: Ingredient normalization

AI can assist with ambiguous ingredient matching and substitutions.

Deterministic backend logic handles:
- Quantity aggregation.
- Unit conversion.
- Serving scaling.
- Pantry deduction.
- Cost calculations.

## 7.8 AI task: Conversational assistant

Supported intent categories:

```text
GENERAL_QUESTION
MEAL_RECOMMENDATION
REPLACE_MEAL
SKIP_MEAL
SWITCH_MEAL_MODE
MODIFY_RECIPE
UPDATE_PREFERENCES
UPDATE_BUDGET
EXPLAIN_NUTRITION
```

AI returns structured intent and proposed actions.

Backend verifies the action against the user's current plan and permissions.

Never directly execute purchases or sensitive operations from an unvalidated LLM response.

# 8. AI Output Schema and Validation

All AI-generated structured data must use predefined JSON schemas.

Recommended validation sequence:

1. Parse AI output.
2. Validate schema.
3. Validate required fields.
4. Resolve food IDs.
5. Normalize units.
6. Check restrictions and allergies.
7. Calculate nutrition.
8. Retrieve real menu/product information where required.
9. Calculate budget.
10. Persist validated data.

If validation fails:
- Reject the invalid result.
- Record the error.
- Retry only where appropriate.
- Notify the frontend using a structured error event if the operation cannot continue.

Do not blindly stream incomplete or unvalidated structured data as a finalized meal.

# 9. Plan Activation and Modification Lifecycle

## 9.1 Plan lifecycle

```text
DRAFT
  |
  v
GENERATING
  |
  v
VALIDATING
  |
  v
READY_FOR_REVIEW
  |
  v
AUTHORIZED
  |
  v
ACTIVE
  |
  +----> PAUSED
  |
  +----> COMPLETED
  |
  +----> CANCELLED
```

## 9.2 Meal lifecycle

Suggested statuses:

`draft`, `planned`, `scheduled`, `processing`, `ordered`, `confirmed`, `completed`, `skipped`, `cancelled`, `needs_user_action`.

## 9.3 Modification after activation

Users can modify active plans, subject to order status and cutoffs.

| State | Modification |
|---|---|
| Draft meal | Fully editable |
| Scheduled action not submitted | Editable |
| Processing action | Depends on operation |
| Confirmed external order | Provider cancellation/modification rules |
| Grocery already purchased | Purchased quantities remain committed |
| Completed meal | Read-only |

Every modification must:
- Create a change history record.
- Increment plan version where appropriate.
- Update nutrition.
- Update budget.
- Update grocery requirements.
- Reconcile affected scheduled actions.
- Avoid executing outdated queued actions.

Use idempotency keys and plan-version checks.

# 10. Grocery Consolidation Architecture

The grocery system should not create one order per meal.

It should:
1. Collect ingredients from all home-cooked recipes.
2. Normalize ingredient names.
3. Convert compatible units.
4. Aggregate quantities.
5. Deduct confirmed pantry quantities.
6. Match actual product packs.
7. Build a consolidated basket.
8. Split into delivery batches only when necessary.

After grocery checkout:
- Purchased quantities become committed inventory.
- Remaining grocery requirements are recalculated.
- Recipe changes must not silently discard already purchased quantities.

Suggested entities:
- `grocery_lists`
- `grocery_items`
- `grocery_products`
- `grocery_delivery_batches`
- `grocery_basket_versions`

# 11. Purchase Authorization Architecture

Plan-level approval is the default product decision.

User reviews:
- Full meal plan.
- Estimated grocery cost.
- Estimated restaurant cost.
- Delivery fees.
- Maximum total spend.
- Grocery spending limit.
- Restaurant spending limit.
- Price variation tolerance.
- Purchase scope.

Before each purchase, backend verifies:
1. Plan is active.
2. Authorization is valid.
3. Purchase is within scope.
4. Current price is within approved tolerance.
5. Product/menu item is still available.
6. Provider authorization requirements are satisfied.
7. No duplicate submission exists.

If any condition fails, set the action to `needs_user_action` and notify the user.

# 12. Careem MCP Integration

Create a provider adapter named `CareemIntegrationService`.

Proposed internal operations:

```typescript
searchRestaurants()
getRestaurantDetails()
getRestaurantMenu()
searchGroceryProducts()
getProductDetails()
createGroceryBasket()
updateGroceryBasket()
submitGroceryOrder()
submitRestaurantOrder()
getOrderStatus()
cancelOrder()
```

These are proposed abstraction methods, not confirmed MCP capabilities.

Before implementing each operation, verify:
- MCP tool availability.
- Authentication.
- Location coverage.
- Restaurant and menu access.
- Grocery catalogue access.
- Product pricing and availability.
- Basket creation and updates.
- Checkout support.
- Payment authorization.
- Order tracking.
- Cancellation.
- Sandbox and production access.

Maintain an integration capability matrix.

If an operation is unavailable, use a clear manual fallback rather than pretending it succeeded.

# 13. Background Jobs and Scheduling

SSE is for interactive AI operations. It should not replace background job processing.

Use a persistent queue and scheduler for:

| Job | Responsibility |
|---|---|
| ExecuteScheduledActionJob | Execute due actions |
| GenerateGroceryListJob | Consolidate ingredients |
| MatchGroceryProductsJob | Match actual products |
| CalculateBudgetJob | Recalculate plan costs |
| SyncExternalOrderJob | Synchronize order status |
| SendNotificationJob | Send notifications |
| ReconcilePlanActionsJob | Reconcile actions after changes |

Suggested scheduled action fields:

```text
id
user_id
meal_plan_id
plan_version
meal_id
action_type
scheduled_at
requested_delivery_at
status
idempotency_key
external_reference
retry_count
last_error
authorization_id
created_at
updated_at
```

Suggested statuses:

`scheduled`, `processing`, `confirmed`, `failed`, `cancelled`, `needs_user_action`, `skipped`.

Do not create a separate cron job per meal. Use a scheduler that finds due records and dispatches queue jobs.

## 13.1 Failure handling

- Use exponential backoff for transient failures.
- Check external operation status before retrying an uncertain purchase.
- Never blindly retry non-idempotent checkout calls.
- Notify users when an action fails.
- Do not silently skip scheduled purchases.

# 14. Database Design

Recommended initial entities:

| Table | Responsibility |
|---|---|
| users | User accounts |
| user_profiles | Personal information |
| nutrition_goals | Nutrition targets |
| food_preferences | Cuisine and food preferences |
| dietary_restrictions | Restrictions |
| user_allergies | Allergies |
| user_routines | Daily schedules |
| meal_plans | Plan header |
| meal_plan_versions | Version history |
| meal_plan_days | Plan days |
| meals | Individual meals |
| recipes | Recipe information |
| recipe_ingredients | Recipe ingredients |
| food_items | Normalized food database |
| restaurant_dishes | External menu records |
| grocery_lists | Consolidated lists |
| grocery_items | Grocery entries |
| grocery_products | External products |
| grocery_delivery_batches | Grocery delivery groups |
| purchase_authorizations | Purchase authorization |
| authorization_limits | Spending limits |
| scheduled_actions | Scheduled execution |
| external_orders | Provider orders |
| meal_change_history | Meal changes |
| conversations | Assistant conversations |
| conversation_messages | Conversation history |
| assistant_actions | Proposed assistant actions |
| ai_generation_requests | AI request tracking |
| ai_generation_events | Optional generation audit |
| notifications | User notifications |
| integration_logs | External API logs |

## 14.1 Important database design considerations

- Use UUIDs for public-facing identifiers.
- Keep external provider IDs separately from internal IDs.
- Use decimal columns for money.
- Store currency explicitly.
- Store timezone-aware timestamps.
- Keep plan versions.
- Keep external order references.
- Store AI request metadata without unnecessarily retaining sensitive prompt data.
- Use database transactions for important plan changes.
- Use unique constraints for idempotency keys.

# 15. Error Handling and API Standards

Normal REST responses should follow a consistent structure.

Success:

```json
{
  "success": true,
  "data": {},
  "meta": {}
}
```

Error:

```json
{
  "success": false,
  "error": {
    "code": "MEAL_PLAN_NOT_FOUND",
    "message": "Meal plan not found."
  }
}
```

SSE errors should use:

```text
event: stream.error
data: {
  "request_id": "req_123",
  "code": "AI_GENERATION_FAILED",
  "message": "Unable to generate the meal plan."
}

```

Do not expose:
- Raw AI provider exceptions.
- Internal API credentials.
- Stack traces.
- Sensitive user profile information.
- Internal prompts or provider configuration.

# 16. Security, Reliability and Observability

## Security
- Authenticate every protected endpoint.
- Enforce user ownership on all resource IDs.
- Keep AI and Careem credentials on the backend.
- Validate all AI output.
- Enforce authorization at execution time.
- Rate-limit expensive AI endpoints.
- Apply idempotency to purchase operations.
- Protect against prompt injection from external content.
- Avoid logging sensitive personal information unnecessarily.

## SSE reliability
- Disable reverse proxy buffering.
- Verify load balancer idle timeouts.
- Send heartbeats.
- Abort upstream AI requests when downstream clients disconnect, where appropriate.
- Avoid opening duplicate generation requests unintentionally.
- Persist generation state.
- Support reconnection or retrieval of the last known generation state.

SSE is not a durable message queue. If a client disconnects, it must be possible to retrieve the generation result or resume from persisted state.

## Observability

Track:
- Request ID.
- AI request ID.
- Time to first event.
- Time to first meaningful content.
- Total generation duration.
- Number of streamed events.
- AI provider latency.
- Validation failures.
- Token usage where available.
- External provider response time.
- Scheduled action execution.
- Order submission failures.

# 17. Recommended Technology Stack

| Layer | Suggested technology |
|---|---|
| Backend | NestJS + TypeScript |
| Database | PostgreSQL |
| Queue | BullMQ |
| Cache | Redis |
| AI communication | HTTP streaming / SSE |
| API documentation | Swagger / OpenAPI |
| Validation | Zod or class-validator |
| External integration | Careem MCP adapter |
| Logging | Structured application logging |
| Testing | Jest + Supertest |
| Frontend streaming | Fetch streaming + SSE parser |
| Mobile streaming | HTTP streaming client |

Use an AI provider abstraction so the backend is not tied to one model vendor.

# 18. Implementation Priorities

Do not implement every endpoint before demonstrating the primary user journey.

## Phase 1 — Foundation

- Authentication.
- Profile.
- Preferences.
- Allergies.
- Nutrition goals.
- Meal plan creation.
- Meal plan retrieval.

## Phase 2 — AI streaming foundation

- AI provider client.
- SSE parser.
- Public SSE event contract.
- Backend-to-AI streaming.
- Backend-to-client streaming.
- Disconnect handling.
- Generation persistence.
- Error handling.

This should be completed before implementing the individual AI features.

## Phase 3 — Meal plan generation

- AI meal plan generation.
- Recipe generation.
- Nutrition calculation.
- Meal plan validation.
- Budget calculation.
- Incremental result streaming.
- Plan display.

## Phase 4 — Meal modifications

- Replace meal.
- Skip meal.
- Switch mode.
- Regenerate meal.
- Change servings.
- Plan versioning.
- Change history.

## Phase 5 — Grocery and restaurant

- Consolidated grocery list.
- Ingredient aggregation.
- Product matching.
- Restaurant discovery.
- Menu retrieval.
- AI ranking.
- Budget updates.

## Phase 6 — Activation and ordering

- Plan review.
- Purchase authorization.
- Activation.
- Scheduling.
- Careem integration.
- One verified order workflow.
- Order status.
- Notifications.

## Phase 7 — AI assistant

- Conversation management.
- Context-aware AI streaming.
- Intent classification.
- Action proposals.
- Action confirmation.
- Preference modification.

For the hackathon, a reliable end-to-end flow is more valuable than implementing every advanced capability incompletely.

# 19. MVP API Implementation Checklist

## Foundation
- [ ] `POST /auth/register`
- [ ] `POST /auth/login`
- [ ] `GET /profile`
- [ ] `PUT /profile`
- [ ] `PUT /profile/body`
- [ ] `PUT /profile/goals`
- [ ] `PUT /profile/preferences`
- [ ] `PUT /profile/restrictions`
- [ ] `PUT /profile/routine`
- [ ] `PUT /profile/budget`

## AI and meal plans
- [ ] `POST /meal-plans`
- [ ] `GET /meal-plans`
- [ ] `GET /meal-plans/{id}`
- [ ] `POST /meal-plans/{id}/generate/stream`
- [ ] `GET /meal-plans/{id}/generation-status`
- [ ] `POST /meal-plans/{id}/validate`
- [ ] `GET /meal-plans/{id}/summary`

## Meal management
- [ ] `GET /meals/{id}`
- [ ] `POST /meals/{id}/replace/stream`
- [ ] `POST /meals/{id}/skip`
- [ ] `POST /meals/{id}/switch-mode/stream`
- [ ] `POST /recipes/generate/stream`

## Grocery and budget
- [ ] `POST /meal-plans/{id}/groceries/generate`
- [ ] `GET /meal-plans/{id}/groceries`
- [ ] `GET /meal-plans/{id}/budget`

## Activation
- [ ] `POST /meal-plans/{id}/authorization`
- [ ] `POST /meal-plans/{id}/authorization/approve`
- [ ] `POST /meal-plans/{id}/activate`
- [ ] `GET /scheduled-actions`

## Careem integration
- [ ] Verify MCP capabilities.
- [ ] Implement adapter for verified capabilities.
- [ ] Test one real supported workflow.
- [ ] Implement external order status tracking.

## AI assistant
- [ ] `POST /assistant/conversations`
- [ ] `POST /assistant/conversations/{id}/messages/stream`
- [ ] `POST /assistant/actions/{id}/confirm`
- [ ] `POST /assistant/actions/{id}/reject`

# 20. Primary End-to-End Flow

The first complete implementation should follow this sequence:

```text
1. User completes onboarding
          |
          v
2. Frontend creates meal plan
          |
          v
3. Frontend opens SSE generation endpoint
          |
          v
4. Backend retrieves user context
          |
          v
5. Backend calls AI service using SSE
          |
          v
6. AI streams meal plan proposal
          |
          v
7. Backend validates and enriches results
          |
          v
8. Backend streams validated days to client
          |
          v
9. Backend completes nutrition and budget
          |
          v
10. Frontend displays complete plan
          |
          v
11. User modifies meals if required
          |
          v
12. Backend updates plan and groceries
          |
          v
13. User reviews and approves spending limits
          |
          v
14. User activates plan
          |
          v
15. Backend creates scheduled actions
          |
          v
16. Queue workers execute eligible actions
          |
          v
17. Backend tracks orders and sends notifications
```

# 21. Final Architecture Decisions

The following decisions should guide development:

1. Modular backend rather than tightly coupled controllers.
2. REST for normal business APIs.
3. SSE for AI-driven interactive operations.
4. AI calls originate exclusively from the backend.
5. Backend parses and maps AI SSE events to the public event contract.
6. Backend forwards useful events with minimal latency.
7. Structured AI output is validated before being persisted as finalized business data.
8. Nutrition and financial calculations remain deterministic backend responsibilities.
9. Actual restaurant and grocery catalogue data must come from providers.
10. Grocery ingredients are consolidated across the plan.
11. Plan-level purchase authorization is enforced before each scheduled purchase.
12. Pre- and post-activation meal modifications are supported within lifecycle restrictions.
13. Scheduled actions are persistent records executed through background workers.
14. Careem MCP is isolated behind an integration adapter.
15. SSE is not used as a replacement for background jobs or durable state.
16. All external purchase operations must be idempotent or reconciled before retry.

**The most important architecture rule:**

AI should be responsible for intelligence and recommendations. The backend should be responsible for truth, validation, business rules, authorization, persistence and execution.

This keeps NutriPilot responsive, reliable, secure and extensible.
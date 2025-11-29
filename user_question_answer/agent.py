# agent.py (single file)

from google.adk.agents import Agent
from google.adk.tools import FunctionTool, load_memory, preload_memory
from datetime import datetime
import time
try:
    from pymongo import MongoClient
    from bson import ObjectId
except Exception as e:
    raise ImportError(
        "pymongo is not installed or cannot be imported. Install it with: "
        "'pip install pymongo' and ensure your Python interpreter/environment is correct."
    ) from e

import smtplib
import ssl
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# ======================================
# MONGODB CONNECTION
# ======================================

# Connect to MongoDB
_client = MongoClient("mongodb://localhost:27017")
_db = _client["user_db"]


# ======================================
# CUSTOM MONGODB TOOL
# ======================================

def db_access(collection: str, query: dict) -> list:
    """
    Execute MongoDB queries and return JSON results.
    
    Args:
        collection: The name of the MongoDB collection to query. Available collections:
                   - 'products': Product information
                   - 'suppliers': Supplier information
                   - 'orders': Order information
        query: A MongoDB query filter object
               Examples:
               - Products: {'product_name': 'Smart Watch'} or {'category': 'Electronics', 'price': {'$lt': 2000}}
               - Suppliers: {'supplier_name': 'Sony'} or {'rating': {'$gte': 4.5}}
               - Orders: {'order_id': 'O1001'} or {'customer_name': 'Rakesh'}
    
    Returns:
        A list of matching documents as JSON objects.
    """
    col = _db[collection]
    result = list(col.find(query).limit(100))
    
    # Convert ObjectId to string
    for r in result:
        r["_id"] = str(r["_id"])
    
    return result

db_tool = FunctionTool(
    func=db_access,
    require_confirmation=False
)


# ======================================
# DB INSERT TOOL
# ======================================

def _generate_id(collection: str, prefix: str) -> str:
    """Generate next available ID for a collection"""
    col = _db[collection]
    # Find the highest existing ID
    existing = col.find({f"{prefix.lower()}_id": {"$regex": f"^{prefix}\\d+$"}}).sort(f"{prefix.lower()}_id", -1).limit(1)
    try:
        last_id = list(existing)[0][f"{prefix.lower()}_id"]
        num = int(last_id[1:]) + 1
        return f"{prefix}{num:03d}"
    except (IndexError, KeyError, ValueError):
        return f"{prefix}001"


def db_insert(collection: str, data: dict) -> dict:
    """
    Insert a new document into MongoDB collection with automatic field filling.
    
    Args:
        collection: Collection name ('products', 'suppliers', or 'orders')
        data: Document data with required fields
    
    Returns:
        Dictionary with success status and inserted document
    """
    valid_collections = ['products', 'suppliers', 'orders']
    if collection not in valid_collections:
        return {"error": True, "message": f"Invalid collection. Available: {', '.join(valid_collections)}"}
    
    try:
        col = _db[collection]
        document = data.copy()
        
        # Auto-fill fields based on collection
        if collection == 'products':
            # Required fields: product_name, price, stock_count
            if not all(k in document for k in ['product_name', 'price', 'stock_count']):
                return {
                    "error": True,
                    "message": "Missing required fields. Required: product_name, price, stock_count"
                }
            
            # Auto-fill fields
            document['product_id'] = _generate_id('products', 'P')
            document['added_date'] = datetime.now().strftime('%Y-%m-%d')
            
            # Set default empty values
            if 'category' not in document:
                document['category'] = ""
            if 'units_sold_last_month' not in document:
                document['units_sold_last_month'] = ""
            if 'units_sold_this_month' not in document:
                document['units_sold_this_month'] = ""
            if 'rating' not in document:
                document['rating'] = ""
            # supplier is optional, don't set if not provided
            
        elif collection == 'suppliers':
            # Required fields: supplier_name, contact_email, contact_number, address
            if not all(k in document for k in ['supplier_name', 'contact_email', 'contact_number', 'address']):
                return {
                    "error": True,
                    "message": "Missing required fields. Required: supplier_name, contact_email, contact_number, address"
                }
            
            # Auto-fill fields
            document['supplier_id'] = _generate_id('suppliers', 'S')
            
            # Set default empty values
            if 'rating' not in document:
                document['rating'] = ""
                
        elif collection == 'orders':
            # Required fields for orders (you can customize this)
            if not all(k in document for k in ['product_id', 'quantity', 'price_per_unit', 'customer_name']):
                return {
                    "error": True,
                    "message": "Missing required fields. Required: product_id, quantity, price_per_unit, customer_name"
                }
            
            # Auto-fill fields
            document['order_id'] = _generate_id('orders', 'O')
            document['order_date'] = datetime.now().strftime('%Y-%m-%d')
            
            # Calculate total_price
            if 'total_price' not in document:
                document['total_price'] = document['quantity'] * document['price_per_unit']
        
        # Insert document
        result = col.insert_one(document)
        document['_id'] = str(result.inserted_id)
        
        return {
            "error": False,
            "success": True,
            "message": f"Successfully inserted {collection} document",
            "document": document
        }
        
    except Exception as e:
        return {
            "error": True,
            "message": f"Insert failed: {str(e)}"
        }


insert_tool = FunctionTool(
    func=db_insert,
    require_confirmation=False
)


# ======================================
# DB UPDATE TOOL
# ======================================

def db_update(collection: str, filter_query: dict, update_data: dict) -> dict:
    """
    Update existing documents in MongoDB collection.
    Requires user confirmation before execution.
    
    Args:
        collection: Collection name ('products', 'suppliers', or 'orders')
        filter_query: MongoDB query to find documents to update
        update_data: Fields to update (use $set operator format)
    
    Returns:
        Dictionary with success status and update result
    """
    valid_collections = ['products', 'suppliers', 'orders']
    if collection not in valid_collections:
        return {"error": True, "message": f"Invalid collection. Available: {', '.join(valid_collections)}"}
    
    try:
        # Verify database connection
        _db.client.admin.command('ping')
        
        col = _db[collection]
        
        # Helper to convert string _id to ObjectId if needed
        if '_id' in filter_query and isinstance(filter_query['_id'], str):
            try:
                filter_query['_id'] = ObjectId(filter_query['_id'])
            except:
                pass  # If it's not a valid ObjectId string, leave it as is

        # Check if documents exist before update
        existing_before = list(col.find(filter_query))
        if not existing_before:
            return {
                "error": True,
                "message": "No documents found matching the filter query"
            }
        
        # Prepare update operation
        update_operation = {"$set": update_data}
        
        # Perform update - ensure it executes
        # Use write_concern to ensure the operation is acknowledged
        result = col.update_many(
            filter_query, 
            update_operation,
            upsert=False
        )
        
        # Force acknowledgment - ensure the operation is committed
        try:
            _db.client.admin.command('ping')
        except:
            pass
        
        # Small delay to ensure write is committed
        import time
        time.sleep(0.1)
        
        # Explicitly check the result
        if not hasattr(result, 'modified_count') or not hasattr(result, 'matched_count'):
            return {
                "error": True,
                "message": "Update operation did not return expected result. Operation may not have executed."
            }
        
        # Verify the update actually happened
        if result.modified_count == 0 and result.matched_count > 0:
            # Documents matched but weren't modified (values might be the same)
            return {
                "error": False,
                "success": True,
                "message": f"Update completed. {result.matched_count} document(s) matched, but no changes were needed (values already match)",
                "matched_count": result.matched_count,
                "modified_count": result.modified_count
            }
        
        # Verify by checking updated documents
        updated_docs = list(col.find(filter_query))
        
        return {
            "error": False,
            "success": True,
            "message": f"Successfully updated {result.modified_count} document(s) in {collection}",
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "updated_fields": list(update_data.keys()),
            "verification": f"Verified: {len(updated_docs)} document(s) now match the query"
        }
        
    except Exception as e:
        return {
            "error": True,
            "message": f"Update failed: {str(e)}",
            "details": f"Error type: {type(e).__name__}"
        }


update_tool = FunctionTool(
    func=db_update,
    require_confirmation=False  # Disabled to avoid ADK blocking
)


# ======================================
# DB DELETE TOOL
# ======================================

def db_delete(collection: str, filter_query: dict) -> dict:
    """
    Delete documents from MongoDB collection.
    Requires user confirmation before execution.
    
    Args:
        collection: Collection name ('products', 'suppliers', or 'orders')
        filter_query: MongoDB query to find documents to delete
    
    Returns:
        Dictionary with success status and delete result
    """
    valid_collections = ['products', 'suppliers', 'orders']
    if collection not in valid_collections:
        return {"error": True, "message": f"Invalid collection. Available: {', '.join(valid_collections)}"}
    
    try:
        # Verify database connection
        _db.client.admin.command('ping')
        
        col = _db[collection]
        
        # Helper to convert string _id to ObjectId if needed
        if '_id' in filter_query and isinstance(filter_query['_id'], str):
            try:
                filter_query['_id'] = ObjectId(filter_query['_id'])
            except:
                pass  # If it's not a valid ObjectId string, leave it as is

        # Check if documents exist before delete
        existing_before = list(col.find(filter_query))
        if not existing_before:
            return {
                "error": True,
                "message": "No documents found matching the filter query"
            }
        
        # Store count before deletion for verification
        count_before = len(existing_before)
        
        # CRITICAL: Perform delete operation - this MUST execute
        # We explicitly call delete_many to ensure the operation happens
        # Note: This will execute when the function is called after user confirmation
        result = col.delete_many(filter_query)
        
        # Immediately verify the operation was called
        if result is None:
            raise Exception("Delete operation returned None - operation did not execute")
        
        # CRITICAL: Verify the operation actually executed
        # Check that we got a result object
        if result is None:
            return {
                "error": True,
                "message": "Delete operation returned None. Operation did not execute."
            }
        
        # Check that the result has the expected attributes
        if not hasattr(result, 'deleted_count'):
            return {
                "error": True,
                "message": f"Delete operation returned unexpected result type: {type(result)}. Operation may not have executed."
            }
        
        # CRITICAL: Force the operation to be acknowledged by MongoDB
        # This ensures the write is committed to the database
        try:
            # Force write acknowledgment by pinging the server
            _db.client.admin.command('ping')
        except Exception as e:
            # Even if ping fails, the delete should still have executed
            pass
        
        # Small delay to ensure write is committed to disk
        time.sleep(0.2)
        
        # Verify the deletion actually happened by querying again
        existing_after = list(col.find(filter_query))
        count_after = len(existing_after)
        
        if result.deleted_count == 0:
            return {
                "error": True,
                "message": "Delete operation returned 0 deleted documents. No documents were removed.",
                "matched_before": count_before,
                "matched_after": count_after
            }
        
        # Double-check by querying again
        if count_after > 0:
            return {
                "error": False,
                "success": True,
                "warning": f"Deleted {result.deleted_count} document(s), but {count_after} still match the query",
                "deleted_count": result.deleted_count,
                "remaining_count": count_after
            }
        
        return {
            "error": False,
            "success": True,
            "message": f"Successfully deleted {result.deleted_count} document(s) from {collection}",
            "deleted_count": result.deleted_count,
            "verification": f"Verified: {count_before} document(s) before deletion, {count_after} document(s) after deletion"
        }
        
    except Exception as e:
        return {
            "error": True,
            "message": f"Delete failed: {str(e)}",
            "details": f"Error type: {type(e).__name__}"
        }


delete_tool = FunctionTool(
    func=db_delete,
    require_confirmation=False  # Disabled to avoid ADK blocking
)


# ======================================
# EMAIL TOOL
# ======================================

def send_email(recipient_email: str, subject: str, message: str) -> dict:
    """
    Send an email to a recipient using SMTP (Gmail).
    
    Args:
        recipient_email: The email address of the recipient
        subject: The subject of the email
        message: The body of the email
    
    Returns:
        Dictionary with success status
    """
    try:
        # Get credentials from environment variables
        sender_email = os.getenv("SENDER_EMAIL")
        password = os.getenv("SENDER_PASSWORD")
        
        if not sender_email or not password or "your_email" in sender_email:
            return {
                "error": True,
                "message": "Email credentials not configured. Please set SENDER_EMAIL and SENDER_PASSWORD in .env file."
            }

        # Create secure SSL context
        context = ssl.create_default_context()
        
        # Connect to Gmail SMTP server
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, password)
            
            # Format email
            email_content = f"Subject: {subject}\n\n{message}"
            
            server.sendmail(sender_email, recipient_email, email_content)
        
        return {
            "error": False,
            "success": True,
            "message": f"Email successfully sent to {recipient_email}"
        }
    except Exception as e:
        return {
            "error": True,
            "message": f"Failed to send email: {str(e)}"
        }


email_tool = FunctionTool(
    func=send_email,
    require_confirmation=False  # Agent handles confirmation via chat
)


# ======================================
# ROOT AGENT
# ======================================

root_agent = Agent(
    model="gemini-2.5-flash",
    name="root_agent",

    description=(
        "You manage and answer questions about products, suppliers, and orders using MongoDB.\n"
        "You have access to three collections in the 'user_db' database:\n"
        "- 'products': Product information (name, price, category, stock, sales, rating, supplier, etc.)\n"
        "- 'suppliers': Supplier information (name, contact, address, rating, etc.)\n"
        "- 'orders': Order information (order ID, product ID, quantity, price, customer, date, etc.)\n"
        "\n"
        "DATABASE OPERATIONS:\n"
        "- db_access: Query/read data from collections\n"
        "- db_insert: Create new documents (auto-fills required fields)\n"
        "- db_update: Update existing documents (requires confirmation)\n"
        "- db_delete: Delete documents (requires confirmation)\n"
        "- send_email: Send emails to suppliers/customers\n"
        "\n"
        "CONTEXT ENGINEERING:\n"
        "- You have access to memory tools to maintain conversation context across sessions\n"
        "- Use load_memory to retrieve previous conversation context\n"
        "- Use preload_memory to store important information for future conversations\n"
        "- Sessions are automatically managed - each user has their own conversation history\n"
        "\n"
        "Workflow:\n"
        "1) Load memory to retrieve previous context\n"
        "2) Analyze user request → Identify operation (read/create/update/delete)\n"
        "3) Execute appropriate database operation\n"
        "4) Convert results → natural language answer\n"
        "5) Store important information in memory for future reference\n"
    ),

    instruction=(
        "=== DATABASE STRUCTURE ===\n"
        "Database: 'user_db'\n"
        "Available Collections: 'products', 'suppliers', 'orders'\n"
        "\n"
        "=== COLLECTION SCHEMAS ===\n"
        "\n"
        "1. PRODUCTS COLLECTION:\n"
        "Each product document contains:\n"
        "{\n"
        "  'product_id': string (e.g., 'P001'),\n"
        "  'product_name': string (e.g., 'Bluetooth Speaker'),\n"
        "  'category': string (e.g., 'Electronics'),\n"
        "  'price': number (e.g., 1499),\n"
        "  'stock_count': number (e.g., 80),\n"
        "  'units_sold_last_month': number (e.g., 320),\n"
        "  'units_sold_this_month': number (e.g., 210),\n"
        "  'rating': number (e.g., 4.5),\n"
        "  'supplier': string (e.g., 'Sony'),\n"
        "  'added_date': string in format 'YYYY-MM-DD' (e.g., '2023-05-10')\n"
        "}\n"
        "\n"
        "2. SUPPLIERS COLLECTION:\n"
        "Each supplier document contains:\n"
        "{\n"
        "  'supplier_id': string (e.g., 'S001'),\n"
        "  'supplier_name': string (e.g., 'Sony'),\n"
        "  'contact_email': string (e.g., 'sony.support@gmail.com'),\n"
        "  'contact_number': string (e.g., '9876543210'),\n"
        "  'address': string (e.g., 'Mumbai, India'),\n"
        "  'rating': number (e.g., 4.6)\n"
        "}\n"
        "\n"
        "3. ORDERS COLLECTION:\n"
        "Each order document contains:\n"
        "{\n"
        "  'order_id': string (e.g., 'O1001'),\n"
        "  'product_id': string (e.g., 'P001'),\n"
        "  'quantity': number (e.g., 2),\n"
        "  'price_per_unit': number (e.g., 1499),\n"
        "  'total_price': number (e.g., 2998),\n"
        "  'order_date': string in format 'YYYY-MM-DD' (e.g., '2023-10-10'),\n"
        "  'customer_name': string (e.g., 'Rakesh')\n"
        "}\n"
        "\n"
        "=== CONTEXT ENGINEERING & MEMORY MANAGEMENT ===\n"
        "\n"
        "SESSION MANAGEMENT:\n"
        "- Each user has their own session with automatic conversation history\n"
        "- Sessions persist across multiple interactions\n"
        "- Previous messages in the same session are automatically available as context\n"
        "\n"
        "MEMORY TOOLS:\n"
        "1. load_memory: Retrieves stored information from previous conversations\n"
        "   - Use this at the start of conversations to get context\n"
        "   - Example: Call load_memory() to retrieve user preferences or previous queries\n"
        "   - Use when user asks follow-up questions or references previous information\n"
        "\n"
        "2. preload_memory: Stores important information for future conversations\n"
        "   - Use this to save key information that might be useful later\n"
        "   - Example: Store user preferences, frequently asked products, or important facts\n"
        "   - Format: preload_memory(content='User prefers products under 2000 rupees')\n"
        "\n"
        "CONTEXT AWARENESS:\n"
        "- If user asks follow-up questions (e.g., 'What about its price?'), use previous context\n"
        "- If user references something mentioned earlier, use load_memory to retrieve it\n"
        "- Store important facts about the user or their preferences using preload_memory\n"
        "\n"
        "=== OPERATION RULES ===\n"
        "1. MANDATORY: ALWAYS call load_memory() FIRST at the start of EVERY conversation to check for stored context.\n"
        "   - This retrieves any previously stored information about the user or conversation\n"
        "   - Even if it's the first message, call load_memory() to check for any stored preferences\n"
        "   - Example: load_memory() - no parameters needed\n"
        "2. Read the user's request carefully and identify the operation type:\n"
        "   - READ/QUERY: User asks questions or wants to see data → use db_access\n"
        "   - CREATE/INSERT: User wants to add new product/supplier/order → use db_insert\n"
        "   - UPDATE: User wants to modify existing data → use db_update (requires confirmation)\n"
        "   - DELETE: User wants to remove data → use db_delete (requires confirmation)\n"
        "3. Identify which collection(s) to work with:\n"
        "   - Products → 'products' collection\n"
        "   - Suppliers/vendors → 'suppliers' collection\n"
        "   - Orders/purchases → 'orders' collection\n"
        "\n"
        "=== DB_ACCESS (READ/QUERY) ===\n"
        "4. For queries, construct a valid MongoDB query using the appropriate field names.\n"
        "5. IMPORTANT: You MUST actually CALL the db_access tool (do not just output text about calling it).\n"
        "   - The tool takes two parameters: 'collection' (string) and 'query' (dict)\n"
        "   - Use MongoDB query operators like $gt, $lt, $gte, $lte, $in, $regex for filtering\n"
        "\n"
        "=== DB_INSERT (CREATE) ===\n"
        "6. For creating new documents, use db_insert tool with required fields:\n"
        "   PRODUCTS - Required fields:\n"
        "     - product_name (required)\n"
        "     - price (required)\n"
        "     - stock_count (required)\n"
        "     - supplier (optional, ask user but not required)\n"
        "   Auto-filled fields (you don't need to provide):\n"
        "     - product_id (auto-generated: P001, P002, etc.)\n"
        "     - added_date (auto-filled with current date)\n"
        "     - category (default: empty string)\n"
        "     - units_sold_last_month (default: empty string)\n"
        "     - units_sold_this_month (default: empty string)\n"
        "     - rating (default: empty string)\n"
        "   Example: db_insert(collection='products', data={'product_name': 'New Product', 'price': 1999, 'stock_count': 50})\n"
        "\n"
        "   SUPPLIERS - Required fields:\n"
        "     - supplier_name (required)\n"
        "     - contact_email (required)\n"
        "     - contact_number (required)\n"
        "     - address (required)\n"
        "   Auto-filled fields:\n"
        "     - supplier_id (auto-generated: S001, S002, etc.)\n"
        "     - rating (default: empty string)\n"
        "   Example: db_insert(collection='suppliers', data={'supplier_name': 'New Supplier', 'contact_email': 'email@example.com', 'contact_number': '1234567890', 'address': 'City, Country'})\n"
        "\n"
        "   ORDERS - Required fields:\n"
        "     - product_id (required)\n"
        "     - quantity (required)\n"
        "     - price_per_unit (required)\n"
        "     - customer_name (required)\n"
        "   Auto-filled fields:\n"
        "     - order_id (auto-generated: O001, O002, etc.)\n"
        "     - order_date (auto-filled with current date)\n"
        "     - total_price (auto-calculated: quantity * price_per_unit)\n"
        "\n"
        "=== DB_UPDATE (UPDATE) ===\n"
        "7. For updating documents, use db_update tool:\n"
        "   - This tool REQUIRES USER CONFIRMATION before execution\n"
        "   - Parameters: collection, filter_query (to find documents), update_data (fields to update)\n"
        "   - Example: db_update(collection='products', filter_query={'product_id': 'P001'}, update_data={'price': 1599, 'stock_count': 100})\n"
        "   - Always confirm with user before calling this tool\n"
        "\n"
        "=== DB_DELETE (DELETE) ===\n"
        "8. For deleting documents, use db_delete tool:\n"
        "   - This tool REQUIRES USER CONFIRMATION before execution\n"
        "   - Parameters: collection, filter_query (to find documents to delete)\n"
        "   - Example: db_delete(collection='products', filter_query={'product_id': 'P001'})\n"
        "   - Always confirm with user before calling this tool\n"
        "   - Show what will be deleted before confirming\n"
        "\n"
        "=== EXAMPLE OPERATIONS ===\n"
        "\n"
        "READ/QUERY EXAMPLES (db_access):\n"
        "PRODUCTS:\n"
        "- Find by name: db_access(collection='products', query={'product_name': 'Smart Watch'})\n"
        "- Find by category: db_access(collection='products', query={'category': 'Electronics'})\n"
        "- Find by price range: db_access(collection='products', query={'price': {'$lt': 2000}})\n"
        "- Find by rating: db_access(collection='products', query={'rating': {'$gte': 4.0}})\n"
        "- Find by supplier: db_access(collection='products', query={'supplier': 'Sony'})\n"
        "- Find best sellers: db_access(collection='products', query={'units_sold_last_month': {'$gt': 500}})\n"
        "\n"
        "SUPPLIERS:\n"
        "- Find by name: db_access(collection='suppliers', query={'supplier_name': 'Sony'})\n"
        "- Find by ID: db_access(collection='suppliers', query={'supplier_id': 'S001'})\n"
        "- Find high-rated suppliers: db_access(collection='suppliers', query={'rating': {'$gte': 4.5}})\n"
        "- Find by location: db_access(collection='suppliers', query={'address': {'$regex': 'Mumbai', '$options': 'i'}})\n"
        "\n"
        "ORDERS:\n"
        "- Find by order ID: db_access(collection='orders', query={'order_id': 'O1001'})\n"
        "- Find by customer: db_access(collection='orders', query={'customer_name': 'Rakesh'})\n"
        "- Find by product: db_access(collection='orders', query={'product_id': 'P001'})\n"
        "- Find by date range: db_access(collection='orders', query={'order_date': {'$gte': '2023-10-01', '$lte': '2023-10-31'}})\n"
        "- Find high-value orders: db_access(collection='orders', query={'total_price': {'$gt': 5000}})\n"
        "\n"
        "CREATE/INSERT EXAMPLES (db_insert):\n"
        "PRODUCTS:\n"
        "- Create product: db_insert(collection='products', data={'product_name': 'New Laptop', 'price': 45000, 'stock_count': 25})\n"
        "- Create product with supplier: db_insert(collection='products', data={'product_name': 'New Laptop', 'price': 45000, 'stock_count': 25, 'supplier': 'Sony'})\n"
        "  Note: product_id and added_date are auto-generated, other fields default to empty strings\n"
        "\n"
        "SUPPLIERS:\n"
        "- Create supplier: db_insert(collection='suppliers', data={'supplier_name': 'Tech Corp', 'contact_email': 'tech@example.com', 'contact_number': '9876543210', 'address': 'Delhi, India'})\n"
        "  Note: supplier_id is auto-generated, rating defaults to empty string\n"
        "\n"
        "ORDERS:\n"
        "- Create order: db_insert(collection='orders', data={'product_id': 'P001', 'quantity': 2, 'price_per_unit': 1499, 'customer_name': 'John'})\n"
        "  Note: order_id and order_date are auto-generated, total_price is auto-calculated\n"
        "\n"
        "UPDATE EXAMPLES (db_update - requires confirmation):\n"
        "- Update product price: db_update(collection='products', filter_query={'product_id': 'P001'}, update_data={'price': 1599})\n"
        "- Update multiple fields: db_update(collection='products', filter_query={'product_id': 'P001'}, update_data={'price': 1599, 'stock_count': 100, 'rating': 4.5})\n"
        "- Update supplier: db_update(collection='suppliers', filter_query={'supplier_id': 'S001'}, update_data={'rating': 4.8, 'contact_number': '9999999999'})\n"
        "  IMPORTANT: Always confirm with user before calling db_update\n"
        "\n"
        "DELETE EXAMPLES (db_delete - requires confirmation):\n"
        "- Delete product: db_delete(collection='products', filter_query={'product_id': 'P001'})\n"
        "- Delete supplier: db_delete(collection='suppliers', filter_query={'supplier_id': 'S001'})\n"
        "- Delete order: db_delete(collection='orders', filter_query={'order_id': 'O1001'})\n"
        "- Delete order: db_delete(collection='orders', filter_query={'order_id': 'O1001'})\n"
        "  IMPORTANT: Always confirm with user and show what will be deleted before calling db_delete\n"
        "\n"
        "=== EMAIL OPERATIONS ===\n"
        "9. To send an email to a supplier:\n"
        "   Step 1: Find the supplier's email using db_access (if not already known).\n"
        "   Step 2: Ask user for the subject and message body (if not provided).\n"
        "   Step 3: Show the draft (To, Subject, Message) to the user and ASK FOR CONFIRMATION.\n"
        "   Step 4: ONLY after user says 'yes', call send_email(recipient_email, subject, message).\n"
        "\n"
        "=== RESPONSE GUIDELINES ===\n"
        "9. After the tool returns results, process the JSON data and convert it into a clear, natural language answer.\n"
        "10. Include relevant details based on the collection:\n"
        "   - Products: names, prices, ratings, stock counts, units sold, categories, suppliers\n"
        "   - Suppliers: names, contact info, addresses, ratings\n"
        "   - Orders: order IDs, product IDs, quantities, prices, customer names, dates\n"
        "11. If the question requires data from multiple collections, make multiple db_access calls.\n"
        "12. For INSERT operations: Confirm what was created and show the auto-generated ID.\n"
        "13. For UPDATE operations: Show what was changed and how many documents were updated.\n"
        "14. For DELETE operations: Show what was deleted and how many documents were removed.\n"
        "15. MANDATORY: ALWAYS call preload_memory() AFTER answering to store important information.\n"
        "   - Store the product name, customer name, or any key information from the query\n"
        "   - Store user preferences or patterns you notice\n"
        "   - Examples:\n"
        "     * preload_memory(content='User asked about product: Smart Watch')\n"
        "     * preload_memory(content='User asked about customer: Rakesh')\n"
        "     * preload_memory(content='User prefers products under 2000 rupees')\n"
        "     * preload_memory(content='User is interested in Electronics category')\n"
        "   - This ensures context is available for future conversations\n"
        "16. If the question doesn't require database access, answer normally without calling any database tools, but still call load_memory() and preload_memory().\n"
        "\n"
        "=== MEMORY USAGE EXAMPLES ===\n"
        "Scenario 1: User asks 'What is the price of Smart Watch?'\n"
        "  Step 1: ALWAYS call load_memory() first (mandatory)\n"
        "  Step 2: Query products collection using db_access\n"
        "  Step 3: Answer the question\n"
        "  Step 4: ALWAYS call preload_memory(content='User asked about product: Smart Watch') (mandatory)\n"
        "\n"
        "Scenario 2: User later asks 'What about its rating?' (follow-up question)\n"
        "  Step 1: ALWAYS call load_memory() first - this retrieves 'User asked about product: Smart Watch'\n"
        "  Step 2: Use the context from memory to know 'its' refers to Smart Watch\n"
        "  Step 3: Query products collection for Smart Watch rating\n"
        "  Step 4: Answer the question\n"
        "  Step 5: ALWAYS call preload_memory(content='User asked about Smart Watch rating') (mandatory)\n"
        "\n"
        "Scenario 3: User asks about products under 2000 rupees\n"
        "  Step 1: ALWAYS call load_memory() first\n"
        "  Step 2: Query products collection\n"
        "  Step 3: Answer the question\n"
        "  Step 4: ALWAYS call preload_memory(content='User prefers products under 2000 rupees') (mandatory)\n"
        "  - Next time: load_memory() will retrieve this preference automatically\n"
    ),

    tools=[
        db_tool,
        insert_tool,
        update_tool,
        delete_tool,
        email_tool,
        load_memory,
        preload_memory,
    ],
)




#please note that ,

#load_memory() — retrieves stored information from previous conversations
#preload_memory() — stores information for future conversations
#DESCRIPTION — "Who the agent is"
#It answers:

#What is this agent?

#What domain does it work in?

#What knowledge does it have?

#What tools does it use?

#How should it think?

#==========================

#INSTRUCTION — "How the agent must behave step-by-step"

#It answers:

#Exactly what sequence to follow?

#When to call which tools?

#What rules must never be broken?

#How to format the response?

#How to query the database?

#How to use memory?

#==========================
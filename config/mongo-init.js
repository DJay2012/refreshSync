// MongoDB initialization script
// This script runs when the MongoDB container starts for the first time

// Create database
db = db.getSiblingDB('your_database');

// Create collections with indexes
db.createCollection('social_feeds');
db.createCollection('articles');
db.createCollection('social_feed_tags');
db.createCollection('social_feed_similar');
db.createCollection('article_tags');
db.createCollection('article_similar');

// Create indexes for better performance
db.social_feeds.createIndex({ "socialFeedId": 1 }, { unique: true });
db.social_feeds.createIndex({ "feedData.feedDate": 1 });
db.social_feeds.createIndex({ "publicationInfo.id": 1 });

db.articles.createIndex({ "articleId": 1 }, { unique: true });
db.articles.createIndex({ "articleData.articleDate": 1 });
db.articles.createIndex({ "publicationInfo.id": 1 });

db.social_feed_tags.createIndex({ "socialFeedId": 1 });
db.social_feed_tags.createIndex({ "company.id": 1 });

db.social_feed_similar.createIndex({ "parentSocialFeedId": 1 });

db.article_tags.createIndex({ "articleId": 1 });
db.article_tags.createIndex({ "company.id": 1 });

db.article_similar.createIndex({ "parentArticleId": 1 });

print('Database initialized successfully');
